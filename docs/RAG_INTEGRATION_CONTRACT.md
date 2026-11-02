# RAG Integration Contract — `rag/` ↔ Django

Owner: Developer A (`feature/rag-engine`). Consumer: Developer B (`feature/django-product`).
Status: **implemented and tested** on the RAG side (contract tests: `rag/tests/test_public_interface.py`).
The LLM provider is **pending** (no provider approved); until one is configured, answers with evidence return
`provider_unavailable`.

Signatures below were read from the code, not from earlier design notes. If a contract test has to change,
this document changes with it and Developer B is told.

## 1. Rules for the Django side

1. Import only the names listed in §3. Everything else in `rag/` is internal.
2. `rag/` never imports Django; Django never touches ChromaDB, the embedding model or the LLM directly.
3. Pass the machine's **business key from the database** (`Machine.machine_id`, e.g. `M-HP200`) — never a raw
   string from the request — and only for active machines the user may access.
4. One `ChromaVectorStore` per process, created once and reused (it loads the ~90 MB embedding model; first
   load ≈10 s, cold). Create it lazily, not at import time, so `manage.py migrate` etc. stay fast.
5. Persist what the RAG layer returns; do not re-derive document IDs, revisions or pages from anything else.

## 2. Process model and storage

- ChromaDB runs embedded (`PersistentClient`) in `CHROMA_DIR` (default `<project root>/chroma_db/`, git-ignored).
  Run **one** Django process for the demo (`runserver`). Several processes writing the same directory is not
  a supported ChromaDB embedded setup and has not been tested.
- `runserver` is multi-threaded. Thread-safety of concurrent index/search calls on one store has **not been
  tested**; as a precaution, serialize write operations (`index_manual`, `remove_manual`,
  `set_current_revision`) with a process-wide lock (see the example document).
- The relational DB is the source of truth. The vector index is derived and can be rebuilt from stored PDFs
  + `Manual` rows by re-running the ingestion sequence for every manual.

## 3. Public interface (exact signatures)

```python
from rag.ingestion.pdf_extractor import extract_pdf
from rag.ingestion.chunker import chunk_document
from rag.embeddings.embedder import get_embedder
from rag.vectorstore.chroma_store import ChromaVectorStore
from rag.generation.llm_client import create_llm_client
from rag.generation.answer_service import (
    answer_question, INSUFFICIENT_EVIDENCE_MESSAGE, PROVIDER_UNAVAILABLE_MESSAGE, GENERATION_ERROR_MESSAGE,
)
from rag.schemas import AnswerResult, AnswerStatus, Citation, IndexSummary, ExtractedDocument
from rag.config import AnswerConfig, VectorStoreConfig, EmbeddingConfig, LLMConfig  # optional overrides
from rag import exceptions
```

| Callable | Signature | Returns |
|---|---|---|
| `extract_pdf` | `extract_pdf(pdf_path: str \| Path, *, machine_id: str, document_id: str, revision: str, source_filename: str \| None = None)` | `ExtractedDocument` |
| `chunk_document` | `chunk_document(document: ExtractedDocument, config: ChunkingConfig \| None = None)` | `list[Chunk]` |
| `get_embedder` | `get_embedder(config: EmbeddingConfig \| None = None)` — cached per config | `SentenceTransformerEmbedder` |
| `ChromaVectorStore` | `ChromaVectorStore(embedder: TextEmbedder, config: VectorStoreConfig \| None = None)` | store |
| `store.index_manual` | `index_manual(chunks: Sequence[Chunk], *, make_current: bool = True)` | `IndexSummary` |
| `store.remove_manual` | `remove_manual(*, machine_id: str, document_id: str, revision: str \| None)` — `revision` required; `None` = all revisions | `int` records removed |
| `store.set_current_revision` | `set_current_revision(*, machine_id: str, document_id: str, revision: str)` | `int` records whose flag changed |
| `store.search` | `search(query: str, *, machine_id: str, limit: int = 8)` (used internally by `answer_question`) | `list[SearchResult]` |
| `store.count` | `count()` | `int` |
| `create_llm_client` | `create_llm_client(config: LLMConfig \| None = None)` — reads env when `None` | `LLMClient \| None` (`None` = no provider) |
| `answer_question` | `answer_question(question: str, machine_id: str, *, store: SearchableStore, llm: LLMClient \| None, config: AnswerConfig \| None = None)` | `AnswerResult` |

Configs default to environment variables (`rag/config.py`, `.env.example`); pass explicit config objects only
in tests or for overrides.

## 4. Return structures (`rag/schemas.py`, frozen dataclasses)

```python
class AnswerStatus(StrEnum):            # str subclass: AnswerStatus.ANSWERED == "answered"
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    GENERATION_ERROR = "generation_error"

AnswerResult(status: AnswerStatus, answer: str, citations: tuple[Citation, ...], machine_id: str, question: str,
             retrieval: RetrievalInfo, llm_model: str | None, rejection_reasons: tuple[str, ...] = (),
             error: str | None = None)

Citation(source_tag: str, chunk_id: str, machine_id: str, document_id: str, revision: str, pdf_page: int,
         source_filename: str, passage: str)

RetrievalInfo(limit: int, max_distance: float | None, retrieved_count: int, evidence: tuple[EvidenceRef, ...])
EvidenceRef(source_tag: str, chunk_id: str, document_id: str, revision: str, pdf_page: int, cosine_distance: float)

IndexSummary(machine_id: str, document_id: str, revision: str, source_filename: str, chunks_indexed: int,
             pages_indexed: tuple[int, ...], stale_records_removed: int, is_current: bool, embedding_model: str)

ExtractedDocument(machine_id, document_id, revision, source_filename, page_count: int, pages: tuple[ExtractedPage, ...])
```

`dataclasses.asdict(result)` is JSON-serializable (tested). `question` and `machine_id` in the result are the
trimmed values actually used.

## 5. `answer_question` behaviour

| `status` | `answer` | `citations` | When |
|---|---|---|---|
| `answered` | validated text; each statement ends with `[S#]` tags | ≥ 1 | LLM draft passed citation validation |
| `insufficient_evidence` | `INSUFFICIENT_EVIDENCE_MESSAGE` | `()` | nothing retrieved within `RAG_MAX_DISTANCE`; LLM said insufficient; or draft still unsupported after `RAG_MAX_CORRECTIONS` |
| `provider_unavailable` | `PROVIDER_UNAVAILABLE_MESSAGE` | `()` | evidence exists but `llm is None` |
| `generation_error` | `GENERATION_ERROR_MESSAGE` | `()` | provider raised, or output still malformed after corrections |

- Retrieval always runs first, so `retrieval` is populated for every status. No evidence ⇒ `insufficient_evidence`
  even without a provider.
- `rejection_reasons` / `error`: diagnostics for logs and `Question.error_message`; free of secrets; **not** for
  display to engineers.
- `llm_model` is `None` when no LLM was used.

Fixed messages:
- `INSUFFICIENT_EVIDENCE_MESSAGE` = "The available manuals for the selected machine do not contain enough information to answer this question."
- `PROVIDER_UNAVAILABLE_MESSAGE` = "Answer generation is unavailable: no LLM provider is configured."
- `GENERATION_ERROR_MESSAGE` = "The assistant could not generate an answer for this question. Please try again later."

## 6. Citations

Every `Citation` field is copied from the retrieved ChromaDB record that was sent to the LLM under
`source_tag` in **this** request; nothing comes from LLM text. Guarantees (tested): `machine_id` equals the
requested machine; `document_id` + `revision` identify an indexed, current revision; `pdf_page` is the
original one-based PDF page; `passage` is the full chunk text; citations are deduplicated in first-use order.

Django persistence: one `Citation` row per item, `rank` = position (1-based), `manual` = the `Manual` row with
that `document_id` + `revision` (verify `manual.machine == question.machine` before saving), `pdf_page`,
`passage`, `chunk_id`, `source_tag`. `document_name` and the PDF link come from the `Manual` row.

## 7. Exceptions (`rag/exceptions.py`; all subclass `RagError`)

| Exception | Also a | Raised by | Django handling |
|---|---|---|---|
| `InvalidMetadataError` | `ValueError` | `answer_question` (blank/oversized question, missing machine ID), `extract_pdf`, store methods (empty IDs, bad limit) | 400; validate earlier in the serializer |
| `InvalidPdfError` | — | `extract_pdf` (missing, not a PDF, empty, encrypted, no pages) | upload 400, or `Manual.status=FAILED` |
| `PdfExtractionError` | — | `extract_pdf` (a page failed; `.pdf_page` set) | `Manual.status=FAILED`, job error |
| `InvalidChunkError` | `ValueError` | `index_manual` (mixed/invalid chunks; `document_id` already indexed for another machine) | `FAILED`; show message to admin |
| `EmbeddingModelError` | — | `get_embedder`, embedding | `FAILED` (model missing/offline) |
| `VectorStoreError` | — | store open/write/search; `answer_question` propagates it | `Question`/`Manual` `FAILED`, 502 |
| `RevisionNotFoundError` | `VectorStoreError`, `LookupError` | `set_current_revision` (nothing changed) | 404/400 to admin |
| `ConfigError` | `ValueError` | config loaders, `create_llm_client` (unknown provider) | fail fast at startup |

`LLMProviderError` and `MalformedLLMResponseError` are handled inside `answer_question` (→ `generation_error`)
and never reach Django.

## 8. Manual ingestion workflow

```
1. Django validates the upload (role ADMIN, machine active, (document_id, revision) unique, PDF type/size),
   saves the file, creates Manual(status=PROCESSING, is_current=False) + IngestionJob(RUNNING). Commit.
2. document = extract_pdf(manual.file.path, machine_id=machine.machine_id, document_id=..., revision=...,
                          source_filename=manual.original_filename)
3. summary = store.index_manual(chunk_document(document), make_current=False)       # staged, not retrievable
4. DB transaction: Manual(status=INDEXED, page_count=document.page_count); other revisions of the same
   document is_current=False, this one True; IngestionJob(SUCCEEDED, pages_processed=document.page_count,
   chunks_indexed=summary.chunks_indexed, embedding_model=summary.embedding_model). Commit.
5. store.set_current_revision(machine_id=..., document_id=..., revision=...)          # now retrievable
```
- Failure in 2–3: `Manual.status=FAILED`, job `FAILED` with the error text. For a **new** revision also call
  `store.remove_manual(machine_id=..., document_id=..., revision=<new>)` to clear any partial records; the
  previously current revision is untouched. (Embedding happens before any write, so most failures leave the
  store unchanged.)
- Failure in 5 after the DB commit: retrieval keeps serving the previous revision; retry step 5 (admin "reindex").
- Re-indexing the same revision is idempotent (deterministic chunk ids, stale ids removed) and keeps its
  current state.
- `make_current=True` (the default) does steps 3 + 5 in one call — acceptable when DB/vector consistency
  on failure is not a concern (e.g. a seed command).
- A `document_id` can belong to only one machine; reusing it for another raises `InvalidChunkError`.

## 9. Revision switching and removal

- Switch: DB transaction (flip `Manual.is_current`) then `store.set_current_revision(...)`. The store demotes
  the other revisions of that machine + document first, then promotes the target (never two current; briefly
  none). Other documents and machines are untouched. Unknown revision → `RevisionNotFoundError`, no change.
- Remove: `store.remove_manual(machine_id=..., document_id=..., revision="1.0")` (one revision) or
  `revision=None` (all revisions). Filtered by machine, so it can never delete another machine's records.
  Removing the current revision leaves no current revision for that document. Per the database design,
  cited manuals are not deleted from the relational DB (`PROTECT`); remove from the index only when the
  revision should no longer answer questions.

## 10. Machine isolation requirements

Implemented in `rag/` (tested): retrieval filters `machine_id` and `is_current` **inside** the ChromaDB query;
empty machine IDs are rejected; only the selected machine's passages reach the LLM; citations resolving to
another machine are rejected; a post-search check raises on any foreign result.

Required from Django: pass `Machine.machine_id` from the DB row the user selected and is allowed to use; never
call `store.search` / `answer_question` without a machine; keep `Machine.machine_id` immutable (it is stored in
every vector record); verify `manual.machine == question.machine` when saving citations.

## 11. Dependencies

- Single source of truth: `rag/requirements.txt` (pymupdf, torch, sentence-transformers, chromadb). The Django
  requirements must **include** it (`-r rag/requirements.txt`), not repeat or re-pin those packages.
- Install CPU torch first (see `rag/requirements.txt` header), then the combined requirements, then `pip check`.
- `rag/` is a plain package imported from the project root (no packaging, no copy). Put `manage.py` at the
  repository root next to `rag/` (or otherwise run Django with the repository root on `sys.path`).
- Pick a Django release that officially supports Python 3.13. Django is not installed on `feature/rag-engine`.
- RAG environment variables are listed in `.env.example`; Django adds its own below them.

## 12. Compatibility with `docs/API_CONTRACT.md` / `docs/DATABASE_ARCHITECTURE.md`

No RAG interface change is required. Differences are resolved by a thin Django mapping layer:

| # | Difference | Smallest resolution | Owner |
|---|---|---|---|
| 1 | RAG statuses are lowercase (`answered`); API/DB use `ANSWERED`, `INSUFFICIENT_EVIDENCE`, `FAILED` | map in the Django service (`provider_unavailable`/`generation_error` → `FAILED`) | B |
| 2 | API citation has `document_name`, `source_url`, `rank`; RAG `Citation` does not | derive from `Manual` row and list position | B |
| 3 | RAG `Citation.source_tag` links `[S#]` in the answer text; DB `Citation` model outline lacks it | add `source_tag = CharField(max_length=8)` (already listed as proposed in the DB doc) | B |
| 4 | DB `Answer.llm_model` is non-null text; RAG `llm_model` may be `None` | store `""` | B |
| 5 | API/DB `Manual.page_count`, job `pages_processed` | from `ExtractedDocument.page_count` (call `extract_pdf` and `chunk_document` separately, as in §8) | B |
| 6 | Earlier architecture drafts proposed `rag/service.py` with `index_manual(pdf_path, …)` and module-level functions | not built; use §3 (the store methods take chunks). A facade can be added later only if both developers agree | A+B |
| 7 | API failure responses: `provider_unavailable` → 503, `generation_error` / `VectorStoreError` → 502 | as documented in API_CONTRACT §4 | B |
| 8 | `rejection_reasons`, `error`, distances are not in the API | keep server-side (logs, `Question.error_message`) | B |
