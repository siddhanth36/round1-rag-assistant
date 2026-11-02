"""Grounded question answering for one selected machine.

Pipeline (each step can end in abstention):

1. Validate question and machine ID (errors raise ``InvalidMetadataError``).
2. Machine-filtered, current-revision retrieval via the existing vector store ``search``.
3. Relevance filter: drop passages with cosine distance > ``max_distance``.
   Nothing left -> ``insufficient_evidence`` (no LLM call).
4. No LLM configured -> ``provider_unavailable``.
5. LLM sees only the tagged passages (S1..Sn) of this request.
6. Parse + validate the reply (citation_validator). Malformed or unsupported drafts get up
   to ``max_corrections`` correction requests.
7. LLM says insufficient -> ``insufficient_evidence``; still unsupported -> ``insufficient_evidence``
   with rejection reasons; still malformed or provider failure -> ``generation_error``.

Provider failures never raise out of ``answer_question``; vector-store failures do
(``VectorStoreError``) because they are infrastructure faults the caller must log.
"""

from __future__ import annotations

import logging
from typing import Protocol

from rag.config import AnswerConfig, load_answer_config
from rag.exceptions import InvalidMetadataError, LLMProviderError, MalformedLLMResponseError
from rag.generation.citation_validator import STATUS_INSUFFICIENT, parse_llm_response, validate_draft
from rag.generation.llm_client import LLMClient
from rag.generation.prompts import SYSTEM_PROMPT, build_correction_prompt, build_user_prompt
from rag.schemas import AnswerResult, AnswerStatus, EvidenceRef, RetrievalInfo, SearchResult

logger = logging.getLogger(__name__)

INSUFFICIENT_EVIDENCE_MESSAGE = (
    "The available manuals for the selected machine do not contain enough information to answer this question."
)
PROVIDER_UNAVAILABLE_MESSAGE = "Answer generation is unavailable: no LLM provider is configured."
GENERATION_ERROR_MESSAGE = "The assistant could not generate an answer for this question. Please try again later."


class SearchableStore(Protocol):
    """The vector-store operation answer generation depends on (ChromaVectorStore.search)."""

    def search(self, query: str, *, machine_id: str, limit: int = ...) -> list[SearchResult]: ...


def answer_question(
    question: str,
    machine_id: str,
    *,
    store: SearchableStore,
    llm: LLMClient | None,
    config: AnswerConfig | None = None,
) -> AnswerResult:
    """Answer ``question`` for ``machine_id`` using only that machine's current manuals.

    Raises:
        InvalidMetadataError: blank/oversized question or missing machine ID.
        VectorStoreError: the vector store failed.
    """
    config = config or load_answer_config()
    question, machine_id = _validate_inputs(question, machine_id, config)

    retrieved = store.search(question, machine_id=machine_id, limit=config.retrieval_limit)
    evidence = [r for r in retrieved if config.max_distance is None or r.cosine_distance <= config.max_distance]
    sources = {f"S{i}": r for i, r in enumerate(evidence, start=1)}  # per-request tag map
    retrieval = RetrievalInfo(
        limit=config.retrieval_limit,
        max_distance=config.max_distance,
        retrieved_count=len(retrieved),
        evidence=tuple(
            EvidenceRef(tag, r.chunk_id, r.document_id, r.revision, r.pdf_page, r.cosine_distance)
            for tag, r in sources.items()
        ),
    )

    def result(
        status: AnswerStatus,
        answer: str,
        *,
        citations: tuple = (),
        rejection_reasons: tuple[str, ...] = (),
        error: str | None = None,
    ) -> AnswerResult:
        return AnswerResult(
            status=status, answer=answer, citations=citations, machine_id=machine_id, question=question,
            retrieval=retrieval, llm_model=llm.model_name if llm else None,
            rejection_reasons=rejection_reasons, error=error,
        )

    if not sources:
        return result(AnswerStatus.INSUFFICIENT_EVIDENCE, INSUFFICIENT_EVIDENCE_MESSAGE,
                      rejection_reasons=("no retrieved passage within the relevance threshold",))
    if llm is None:
        return result(AnswerStatus.PROVIDER_UNAVAILABLE, PROVIDER_UNAVAILABLE_MESSAGE)

    base_prompt = build_user_prompt(question, machine_id, sources)
    user_prompt = base_prompt
    reasons: tuple[str, ...] = ()
    malformed = False
    for attempt in range(config.max_corrections + 1):
        try:
            raw = llm.complete(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)
        except LLMProviderError as exc:
            logger.warning("LLM provider error for machine %s: %s", machine_id, exc)
            return result(AnswerStatus.GENERATION_ERROR, GENERATION_ERROR_MESSAGE, error=f"LLMProviderError: {exc}")
        except Exception as exc:  # a faulty adapter must not crash the application
            logger.exception("Unexpected LLM client failure for machine %s", machine_id)
            return result(AnswerStatus.GENERATION_ERROR, GENERATION_ERROR_MESSAGE, error=type(exc).__name__)

        try:
            draft = parse_llm_response(raw)
        except MalformedLLMResponseError as exc:
            malformed, reasons = True, (f"malformed response: {exc}",)
        else:
            malformed = False
            if draft.status == STATUS_INSUFFICIENT:
                return result(AnswerStatus.INSUFFICIENT_EVIDENCE, INSUFFICIENT_EVIDENCE_MESSAGE,
                              rejection_reasons=("LLM reported insufficient evidence",))
            outcome = validate_draft(draft, sources, machine_id)
            if outcome.is_valid:
                return result(AnswerStatus.ANSWERED, outcome.answer_text, citations=outcome.citations)
            reasons = outcome.reasons

        logger.info("Draft rejected (attempt %d) for machine %s: %s", attempt + 1, machine_id, reasons)
        user_prompt = f"{base_prompt}\n\nYour previous reply:\n{raw}\n\n{build_correction_prompt(reasons)}"

    if malformed:
        return result(AnswerStatus.GENERATION_ERROR, GENERATION_ERROR_MESSAGE,
                      rejection_reasons=reasons, error="malformed LLM response")
    return result(AnswerStatus.INSUFFICIENT_EVIDENCE, INSUFFICIENT_EVIDENCE_MESSAGE, rejection_reasons=reasons)


def _validate_inputs(question: str, machine_id: str, config: AnswerConfig) -> tuple[str, str]:
    if not isinstance(machine_id, str) or not machine_id.strip():
        raise InvalidMetadataError(f"machine_id must be a non-empty string, got {machine_id!r}")
    if not isinstance(question, str) or not question.strip():
        raise InvalidMetadataError("question must be a non-empty string")
    question = question.strip()
    if len(question) > config.max_question_chars:
        raise InvalidMetadataError(f"question exceeds {config.max_question_chars} characters")
    return question, machine_id.strip()
