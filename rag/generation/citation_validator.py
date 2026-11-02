"""Parse LLM output and validate it against the retrieved evidence of THIS request.

The LLM returns statements with source tags. Validation resolves every tag through the
per-request ``sources`` mapping (tag -> retrieved SearchResult). Nothing the LLM says about
documents, revisions or pages is trusted; citation metadata is copied from the records.

Checks (any failure rejects the whole draft — unsupported statements are never just
stripped of their citations):

* every statement cites at least one tag; every tag (in ``sources`` lists or inline
  ``[S#]`` text) exists in this request's mapping
* every cited record belongs to the selected machine and has sane metadata
* document IDs, revisions and page numbers written in a statement match its cited records
  (or are quoted from the cited passage text, e.g. a cross-reference to another manual)
* error codes (``E17``) and identifiers (``RF-20``, ``AC-L32``) in a statement appear in
  its cited passages
* every number in a statement appears in its cited passages

Limitation: these are lexical checks. They catch fabricated values, codes, identifiers,
pages and documents, but they cannot prove that the wording of a statement is entailed by
its passages (e.g. a wrongly paraphrased relationship between two values that both appear).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from rag.exceptions import MalformedLLMResponseError
from rag.schemas import Citation, SearchResult

STATUS_ANSWERED = "answered"
STATUS_INSUFFICIENT = "insufficient_evidence"

_TAG = re.compile(r"^\[?\s*(S\d+)\s*\]?$")
_INLINE_TAG = re.compile(r"\[\s*(S\d+)\s*\]")
_DOCUMENT_ID = re.compile(r"\b[A-Z]{2,4}-[A-Z]{2,4}-\d{2,3}\b")  # e.g. HP-OPS-01
_PAGE_REF = re.compile(r"\bpages?\s+(\d+)\b", re.IGNORECASE)
_REVISION_REF = re.compile(r"\b(?:rev|revision)\.?\s+(\d+(?:\.\d+)*)\b", re.IGNORECASE)
_ERROR_CODE = re.compile(r"\bE\d{2,3}\b")
_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+\b")  # RF-20, AF-10, AC-L32, M-HP200
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
# U+2010..U+2015 hyphens/dashes and U+2212 minus -> ASCII "-", so "HP\u2011MNT\u201101" is checked as HP-MNT-01.
_DASHES = str.maketrans(dict.fromkeys([*map(chr, range(0x2010, 0x2016)), "\u2212"], "-"))


@dataclass(frozen=True, slots=True)
class DraftStatement:
    text: str
    source_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LLMDraft:
    status: str
    statements: tuple[DraftStatement, ...]


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """``citations`` (deduplicated, in first-use order) and ``answer_text`` are set only when valid."""

    is_valid: bool
    reasons: tuple[str, ...]
    citations: tuple[Citation, ...] = ()
    answer_text: str = ""


def parse_llm_response(raw: str) -> LLMDraft:
    """Parse the LLM reply into an LLMDraft. Extra keys the LLM adds are ignored.

    Raises:
        MalformedLLMResponseError: not a JSON object of the required shape.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise MalformedLLMResponseError("LLM returned an empty response")
    payload = _extract_json_object(raw)
    status = payload.get("status")
    if status not in (STATUS_ANSWERED, STATUS_INSUFFICIENT):
        raise MalformedLLMResponseError(f"status must be {STATUS_ANSWERED!r} or {STATUS_INSUFFICIENT!r}, got {status!r}")
    raw_statements = payload.get("statements", [])
    if not isinstance(raw_statements, list):
        raise MalformedLLMResponseError("statements must be a list")

    statements = []
    for i, item in enumerate(raw_statements, start=1):
        if not isinstance(item, dict):
            raise MalformedLLMResponseError(f"statement {i} must be an object")
        text, sources = item.get("text"), item.get("sources", [])
        if not isinstance(text, str) or not text.strip():
            raise MalformedLLMResponseError(f"statement {i} has no text")
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            raise MalformedLLMResponseError(f"statement {i} sources must be a list of strings")
        statements.append(DraftStatement(text=text.strip(), source_tags=tuple(_normalize_tag(s) for s in sources)))
    return LLMDraft(status=status, statements=tuple(statements))


def validate_draft(draft: LLMDraft, sources: Mapping[str, SearchResult], machine_id: str) -> ValidationOutcome:
    """Validate an ``answered`` draft against this request's tagged evidence."""
    if draft.status != STATUS_ANSWERED:
        return ValidationOutcome(is_valid=False, reasons=("draft is not an answer",))
    if not draft.statements:
        return ValidationOutcome(is_valid=False, reasons=("answered status but no statements",))

    reasons: list[str] = []
    for number, statement in enumerate(draft.statements, start=1):
        reasons.extend(f"statement {number}: {r}" for r in _check_statement(statement, sources, machine_id))
    if reasons:
        return ValidationOutcome(is_valid=False, reasons=tuple(reasons))

    used_tags: list[str] = []
    for statement in draft.statements:
        for tag in _all_tags(statement):
            if tag not in used_tags:
                used_tags.append(tag)
    citations = tuple(_to_citation(tag, sources[tag]) for tag in used_tags)
    answer_text = " ".join(_render_statement(s) for s in draft.statements)
    return ValidationOutcome(is_valid=True, reasons=(), citations=citations, answer_text=answer_text)


# ------------------------------------------------------------------------------- internals


def _check_statement(statement: DraftStatement, sources: Mapping[str, SearchResult], machine_id: str) -> list[str]:
    tags = _all_tags(statement)
    if not tags:
        return ["no source tag cited"]
    unknown = sorted({t for t in tags if t not in sources})
    if unknown:
        return [f"unknown source tag(s) {', '.join(unknown)} (not in this request's evidence)"]

    problems: list[str] = []
    records = [sources[t] for t in tags]
    for tag, record in zip(tags, records, strict=True):
        if record.machine_id != machine_id:
            problems.append(f"{tag} belongs to machine {record.machine_id!r}, not the selected {machine_id!r}")
        if record.pdf_page < 1 or not record.document_id or not record.revision or not record.text.strip():
            problems.append(f"{tag} has invalid source metadata")
    if problems:
        return problems

    # Compare on dash-normalized copies; the displayed answer keeps the original characters.
    text = _INLINE_TAG.sub(" ", statement.text).translate(_DASHES)
    evidence = " ".join(r.text for r in records).translate(_DASHES)

    # A document/page/revision reference is allowed if it is one of the cited records' own
    # values, or if the cited passage itself contains it (a quoted cross-reference such as
    # "request the checks in HP-MNT-01 page 3").
    cited_docs = {r.document_id for r in records} | set(_DOCUMENT_ID.findall(evidence))
    for doc_id in sorted(set(_DOCUMENT_ID.findall(text)) - cited_docs):
        problems.append(f"mentions document {doc_id} which is not among its cited sources")
    cited_pages = {r.pdf_page for r in records} | {int(p) for p in _PAGE_REF.findall(evidence)}
    for page in sorted({int(p) for p in _PAGE_REF.findall(text)} - cited_pages):
        problems.append(f"mentions page {page} which is not a page of its cited sources")
    cited_revisions = {r.revision for r in records} | set(_REVISION_REF.findall(evidence))
    for revision in sorted(set(_REVISION_REF.findall(text)) - cited_revisions):
        problems.append(f"mentions revision {revision} which is not a revision of its cited sources")

    # Remove the references checked above, then require codes/identifiers/numbers to be in the evidence.
    remaining = _PAGE_REF.sub(" ", _REVISION_REF.sub(" ", _DOCUMENT_ID.sub(" ", text)))
    allowed_identifiers = {r.machine_id for r in records}
    for token in sorted(set(_ERROR_CODE.findall(remaining)) | set(_IDENTIFIER.findall(remaining))):
        if token not in allowed_identifiers and not re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", evidence):
            problems.append(f"code/identifier {token} does not appear in its cited passages")
    remaining = _IDENTIFIER.sub(" ", _ERROR_CODE.sub(" ", remaining))
    evidence_numbers = {_normalize_number(n) for n in _NUMBER.findall(evidence)}
    for value in sorted({_normalize_number(n) for n in _NUMBER.findall(remaining)} - evidence_numbers):
        problems.append(f"value {value} does not appear in its cited passages")
    return problems


def _extract_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):  # tolerate a fenced ```json block
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise MalformedLLMResponseError("LLM response contains no JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise MalformedLLMResponseError(f"LLM response is not valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise MalformedLLMResponseError("LLM response JSON must be an object")
    return payload


def _all_tags(statement: DraftStatement) -> tuple[str, ...]:
    """Tags from the ``sources`` list plus any written inline as ``[S#]``, deduplicated in order."""
    return tuple(dict.fromkeys(statement.source_tags + tuple(_INLINE_TAG.findall(statement.text))))


def _normalize_tag(tag: str) -> str:
    match = _TAG.match(tag.strip())
    return match.group(1) if match else tag.strip()


def _normalize_number(value: str) -> str:
    value = value.replace(",", "")
    return value.rstrip("0").rstrip(".") if "." in value else value


def _render_statement(statement: DraftStatement) -> str:
    text = _INLINE_TAG.sub("", statement.text).strip()
    return f"{text} " + "".join(f"[{t}]" for t in _all_tags(statement))


def _to_citation(tag: str, record: SearchResult) -> Citation:
    return Citation(
        source_tag=tag,
        chunk_id=record.chunk_id,
        machine_id=record.machine_id,
        document_id=record.document_id,
        revision=record.revision,
        pdf_page=record.pdf_page,
        source_filename=record.source_filename,
        passage=record.text,
    )
