"""Typed, immutable data structures passed between RAG stages.

Every structure carries the full source identity (machine, document, revision, file,
one-based PDF page) so no downstream stage ever has to guess where text came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """Identity of one manual revision. Supplied explicitly by the caller, never inferred."""

    machine_id: str
    document_id: str
    revision: str
    source_filename: str


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """Text of a single PDF page.

    ``pdf_page`` is the original one-based PDF page index (page 1 = first page of the
    file). It is what citations use; it is never derived from printed page labels.
    ``page_text`` may be empty for a blank page.
    """

    machine_id: str
    document_id: str
    revision: str
    source_filename: str
    pdf_page: int
    page_text: str

    @property
    def is_blank(self) -> bool:
        return not self.page_text.strip()


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """All pages of one manual revision, in PDF order."""

    machine_id: str
    document_id: str
    revision: str
    source_filename: str
    page_count: int
    pages: tuple[ExtractedPage, ...]


@dataclass(frozen=True, slots=True)
class Chunk:
    """A searchable passage. Always belongs to exactly one page of one document revision.

    ``chunk_index`` is the zero-based position of the chunk within its page.
    ``chunk_id`` is deterministic: ``{document_id}:{revision}:p{pdf_page}:c{chunk_index}``.
    """

    chunk_id: str
    chunk_index: int
    machine_id: str
    document_id: str
    revision: str
    source_filename: str
    pdf_page: int
    text: str


@dataclass(frozen=True, slots=True)
class IndexSummary:
    """Result of indexing one document revision into the vector store."""

    machine_id: str
    document_id: str
    revision: str
    source_filename: str
    chunks_indexed: int
    pages_indexed: tuple[int, ...]
    stale_records_removed: int
    is_current: bool
    embedding_model: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One chunk returned by machine-filtered vector search.

    ``cosine_distance`` is the raw ChromaDB cosine distance between the query and chunk
    embeddings (0 = same direction, larger = less similar). It is a ranking signal only,
    NOT a calibrated confidence or probability.
    """

    chunk_id: str
    chunk_index: int
    machine_id: str
    document_id: str
    revision: str
    source_filename: str
    pdf_page: int
    text: str
    cosine_distance: float


class AnswerStatus(StrEnum):
    """Outcome of answer_question()."""

    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    GENERATION_ERROR = "generation_error"


@dataclass(frozen=True, slots=True)
class Citation:
    """A validated citation. Every field comes from the retrieved record, never from the LLM."""

    source_tag: str
    chunk_id: str
    machine_id: str
    document_id: str
    revision: str
    pdf_page: int
    source_filename: str
    passage: str


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """One passage that was sent to the LLM under ``source_tag``."""

    source_tag: str
    chunk_id: str
    document_id: str
    revision: str
    pdf_page: int
    cosine_distance: float


@dataclass(frozen=True, slots=True)
class RetrievalInfo:
    """What retrieval returned and what was passed on as evidence."""

    limit: int
    max_distance: float | None
    retrieved_count: int
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """Structured result of answer_question().

    ``answer`` is the validated answer text (with ``[S#]`` tags) when ``status`` is
    ``answered``; otherwise a fixed system message. ``rejection_reasons`` lists why a
    generated draft was rejected (for audit/debugging); ``error`` summarizes provider or
    parsing failures without secrets.
    """

    status: AnswerStatus
    answer: str
    citations: tuple[Citation, ...]
    machine_id: str
    question: str
    retrieval: RetrievalInfo
    llm_model: str | None
    rejection_reasons: tuple[str, ...] = ()
    error: str | None = None
