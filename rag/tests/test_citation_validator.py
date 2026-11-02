"""Unit tests for rag.generation.citation_validator using real manual chunks as evidence."""

from __future__ import annotations

import json

import pytest

from rag.exceptions import MalformedLLMResponseError
from rag.generation.citation_validator import (
    DraftStatement,
    LLMDraft,
    parse_llm_response,
    validate_draft,
)
from rag.schemas import SearchResult
from rag.tests.support import answer_json, search_result

HP = "M-HP200"


def _chunk(manual_chunks, chunk_id: str):
    return next(c for chunks in manual_chunks.values() for c in chunks if c.chunk_id == chunk_id)


@pytest.fixture()
def hp_sources(manual_chunks) -> dict[str, SearchResult]:
    """S1 = HP-OPS-01 p1 (reference values), S2 = HP-MNT-01 p3 (troubleshooting)."""
    return {
        "S1": search_result(_chunk(manual_chunks, "HP-OPS-01:1.0:p1:c0")),
        "S2": search_result(_chunk(manual_chunks, "HP-MNT-01:1.0:p3:c0")),
    }


def _draft(*statements: tuple[str, list[str]]) -> LLMDraft:
    return parse_llm_response(answer_json(*statements))


# --- parsing -------------------------------------------------------------------------------


def test_parses_answer_and_normalizes_bracketed_tags() -> None:
    draft = parse_llm_response(answer_json(("x", ["[S1]", " S2 "])))
    assert draft == LLMDraft("answered", (DraftStatement("x", ("S1", "S2")),))


def test_parses_fenced_json_and_ignores_extra_keys() -> None:
    raw = '```json\n{"status": "answered", "statements": [{"text": "x", "sources": ["S1"], "page": 99}], "extra": 1}\n```'
    assert parse_llm_response(raw).statements[0].source_tags == ("S1",)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "The pressure is 120 bar.",
        "{not json}",
        "[1, 2]",
        json.dumps({"status": "maybe", "statements": []}),
        json.dumps({"statements": []}),
        json.dumps({"status": "answered", "statements": "S1"}),
        json.dumps({"status": "answered", "statements": [{"text": "", "sources": ["S1"]}]}),
        json.dumps({"status": "answered", "statements": [{"text": "x", "sources": "S1"}]}),
        json.dumps({"status": "answered", "statements": ["x"]}),
    ],
)
def test_malformed_responses_raise(raw: str) -> None:
    with pytest.raises(MalformedLLMResponseError):
        parse_llm_response(raw)


# --- valid citations -------------------------------------------------------------------------


def test_valid_statement_resolves_metadata_from_records(hp_sources) -> None:
    outcome = validate_draft(
        _draft(("Normal hydraulic pressure is 120 to 140 bar during the forming stage.", ["S1"])), hp_sources, HP
    )
    assert outcome.is_valid, outcome.reasons
    (citation,) = outcome.citations
    record = hp_sources["S1"]
    assert (citation.source_tag, citation.chunk_id, citation.machine_id, citation.document_id) == (
        "S1", record.chunk_id, HP, "HP-OPS-01"
    )
    assert (citation.revision, citation.pdf_page, citation.source_filename) == ("1.0", 1, "HP200_Operations_Rev1_0.pdf")
    assert citation.passage == record.text
    assert outcome.answer_text.endswith("[S1]")


def test_multi_source_statements_cite_each_document(hp_sources) -> None:
    outcome = validate_draft(
        _draft(
            ("The pressure-low trigger is below 110 bar for more than 5 seconds during forming.", ["S1"]),
            ("First check the reservoir level relative to MIN and MAX.", ["S2"]),
        ),
        hp_sources,
        HP,
    )
    assert outcome.is_valid, outcome.reasons
    assert [(c.document_id, c.pdf_page) for c in outcome.citations] == [("HP-OPS-01", 1), ("HP-MNT-01", 3)]


def test_inline_tags_are_validated_and_cited(hp_sources) -> None:
    outcome = validate_draft(_draft(("Oil is H46 hydraulic oil [S1].", [])), hp_sources, HP)
    assert outcome.is_valid, outcome.reasons
    assert [c.source_tag for c in outcome.citations] == ["S1"]


def test_quoted_cross_reference_to_other_manual_is_allowed(manual_chunks) -> None:
    sources = {"S1": search_result(_chunk(manual_chunks, "HP-OPS-01:1.0:p2:c0"))}
    text = "For E17, record pressure and cycle stage; place on HOLD and request the checks in HP-MNT-01 page 3."
    assert validate_draft(_draft((text, ["S1"])), sources, HP).is_valid


# --- rejections ------------------------------------------------------------------------------


def _reasons(draft: LLMDraft, sources) -> str:
    outcome = validate_draft(draft, sources, HP)
    assert not outcome.is_valid
    assert outcome.citations == ()
    return " | ".join(outcome.reasons)


def test_unknown_tag_rejected(hp_sources) -> None:
    assert "unknown source tag(s) S9" in _reasons(_draft(("Pressure is 120 to 140 bar.", ["S9"])), hp_sources)


def test_unknown_inline_tag_rejected(hp_sources) -> None:
    assert "S7" in _reasons(_draft(("Pressure is 120 to 140 bar [S7].", ["S1"])), hp_sources)


def test_empty_citation_list_rejected(hp_sources) -> None:
    assert "no source tag cited" in _reasons(_draft(("Pressure is 120 to 140 bar.", [])), hp_sources)


def test_answer_without_statements_rejected(hp_sources) -> None:
    assert "no statements" in _reasons(LLMDraft("answered", ()), hp_sources)


def test_fabricated_page_reference_rejected(hp_sources) -> None:
    reasons = _reasons(_draft(("Normal pressure is 120 to 140 bar (see page 7).", ["S1"])), hp_sources)
    assert "mentions page 7" in reasons


def test_fabricated_document_id_rejected(hp_sources) -> None:
    reasons = _reasons(_draft(("HP-XYZ-99 states normal pressure is 120 to 140 bar.", ["S1"])), hp_sources)
    assert "mentions document HP-XYZ-99" in reasons


def test_document_id_of_uncited_source_rejected(hp_sources) -> None:
    """HP-MNT-01 is in the evidence (S2) but the statement only cites S1."""
    reasons = _reasons(_draft(("HP-MNT-01 lists normal pressure as 120 to 140 bar.", ["S1"])), hp_sources)
    assert "mentions document HP-MNT-01" in reasons


def test_fabricated_revision_rejected(hp_sources) -> None:
    reasons = _reasons(_draft(("Revision 2.0 sets normal pressure to 120 to 140 bar.", ["S1"])), hp_sources)
    assert "mentions revision 2.0" in reasons


def test_metadata_written_by_llm_is_never_used(hp_sources) -> None:
    raw = json.dumps({"status": "answered", "statements": [
        {"text": "Normal pressure is 120 to 140 bar.", "sources": ["S1"], "page": 9, "document_id": "FAKE-01"}
    ]})
    outcome = validate_draft(parse_llm_response(raw), hp_sources, HP)
    assert outcome.is_valid
    assert (outcome.citations[0].document_id, outcome.citations[0].pdf_page) == ("HP-OPS-01", 1)


def test_unsupported_value_rejected(hp_sources) -> None:
    reasons = _reasons(_draft(("Normal hydraulic pressure is 150 to 140 bar.", ["S1"])), hp_sources)
    assert "value 150" in reasons


def test_value_from_uncited_passage_rejected(hp_sources) -> None:
    """65 (high-stop temperature) is in S1, not in S2 which the statement cites."""
    reasons = _reasons(_draft(("The high stop applies at 65 degrees C.", ["S2"])), hp_sources)
    assert "value 65" in reasons


def test_foreign_error_code_and_part_rejected(hp_sources) -> None:
    """AF-10 and E08 exist only in the AC-90 manual."""
    reasons = _reasons(_draft(("Check the AF-10 filter when E08 appears.", ["S2"])), hp_sources)
    assert "AF-10" in reasons and "E08" in reasons


def test_wrong_machine_citation_rejected(hp_sources, manual_chunks) -> None:
    """Even if a foreign record reached the evidence map, citing it is rejected."""
    sources = dict(hp_sources, S3=search_result(_chunk(manual_chunks, "AC-OM-01:1.0:p2:c0")))
    reasons = _reasons(_draft(("E17 means High discharge temperature.", ["S3"])), sources)
    assert "belongs to machine 'M-AC90'" in reasons


def test_one_unsupported_statement_rejects_whole_answer(hp_sources) -> None:
    draft = _draft(
        ("Normal hydraulic pressure is 120 to 140 bar during the forming stage.", ["S1"]),
        ("The pump must be replaced every 900 hours.", ["S1"]),
    )
    assert "statement 2: value 900" in _reasons(draft, hp_sources)


def test_insufficient_draft_is_not_an_answer(hp_sources) -> None:
    outcome = validate_draft(LLMDraft("insufficient_evidence", ()), hp_sources, HP)
    assert not outcome.is_valid


# --- Unicode hyphens (live gpt-oss-120b output used U+2011) ------------------------------------

NBH = "‑"  # NON-BREAKING HYPHEN


@pytest.mark.parametrize("dash", ["‐", NBH, "‒", "–", "—", "―", "−"])
def test_unicode_hyphen_document_id_is_checked_like_ascii(hp_sources, dash) -> None:
    """HP‑MNT‑01 (S2's own document) is accepted; HP‑XYZ‑99 is still caught as fabricated."""
    ok = _draft((f"First check the reservoir level relative to MIN and MAX per HP{dash}MNT{dash}01.", ["S2"]))
    assert validate_draft(ok, hp_sources, HP).is_valid
    assert "mentions document HP-XYZ-99" in _reasons(_draft((f"HP{dash}XYZ{dash}99 says check MIN and MAX.", ["S2"])), hp_sources)


def test_unicode_hyphen_part_present_in_evidence_is_accepted(manual_chunks) -> None:
    """AF‑10 written with U+2011 is recognized as the AF-10 printed in the AC-90 manual."""
    sources = {"S1": search_result(_chunk(manual_chunks, "AC-OM-01:1.0:p3:c0"))}
    draft = _draft((f"The AF{NBH}10 indicator is checked every 100 operating hours.", ["S1"]))
    assert validate_draft(draft, sources, "M-AC90").is_valid


def test_unicode_hyphen_foreign_identifiers_still_rejected(hp_sources) -> None:
    """AF-10 (AC-90 part) and M-AC90 cited against HP-200 evidence must not slip through via U+2011."""
    reasons = _reasons(_draft((f"Replace the AF{NBH}10 filter as on M{NBH}AC90.", ["S2"])), hp_sources)
    assert "code/identifier AF-10" in reasons and "code/identifier M-AC90" in reasons


def test_ascii_identifiers_unchanged(hp_sources) -> None:
    assert validate_draft(_draft(("Check MIN and MAX per HP-MNT-01.", ["S2"])), hp_sources, HP).is_valid
    assert "code/identifier AF-10" in _reasons(_draft(("Check the AF-10 filter.", ["S2"])), hp_sources)


def test_displayed_answer_keeps_original_characters(hp_sources) -> None:
    text = f"Normal hydraulic pressure is 120 to 140 bar during the forming stage {NBH} per HP{NBH}OPS{NBH}01."
    outcome = validate_draft(_draft((text, ["S1"])), hp_sources, HP)
    assert outcome.is_valid
    assert outcome.answer_text == f"{text} [S1]"  # no dash rewriting in what the user sees


def test_unicode_minus_does_not_hide_unsupported_number(hp_sources) -> None:
    assert "value 150" in _reasons(_draft(("Normal pressure is −150 bar.", ["S1"])), hp_sources)
