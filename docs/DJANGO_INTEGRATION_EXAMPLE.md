# Django Integration Example (documentation only)

Illustrative service-layer code for Developer B showing how to call the RAG package as defined in
`docs/RAG_INTEGRATION_CONTRACT.md`. **No Django application exists yet** (`feature/django-product`
contains only the initial commit), so model and field names follow the outline in
`docs/DATABASE_ARCHITECTURE.md` §8 and must be adapted to the real models. The RAG calls, arguments,
return fields and exceptions are exact; they are exercised by `rag/tests/test_public_interface.py`.

Suggested location: a service module such as `assistant/rag_services.py` (views/serializers call these
functions; they never call `rag` directly).

## 1. Shared store and LLM client (one per process)

```python
import logging
import threading

from rag.embeddings.embedder import get_embedder
from rag.generation.llm_client import create_llm_client
from rag.vectorstore.chroma_store import ChromaVectorStore

logger = logging.getLogger(__name__)

_init_lock = threading.Lock()
_write_lock = threading.Lock()  # precaution: serialize index/remove/set_current (contract §2)
_store: ChromaVectorStore | None = None
_llm = None
_llm_loaded = False


def get_store() -> ChromaVectorStore:
    """Created on first use (loads the embedding model, ~10 s cold), then reused."""
    global _store
    with _init_lock:
        if _store is None:
            _store = ChromaVectorStore(get_embedder())  # CHROMA_DIR / EMBEDDING_* from the environment
        return _store


def get_llm():
    """LLMClient, or None while no provider is configured (answers then return provider_unavailable)."""
    global _llm, _llm_loaded
    with _init_lock:
        if not _llm_loaded:
            _llm = create_llm_client()  # raises rag.exceptions.ConfigError for an unknown LLM_PROVIDER
            _llm_loaded = True
        return _llm
```

Do not create the store in `AppConfig.ready()` — that would load the model for `migrate`, `makemigrations`
and every test run.

## 2. Asking a question

```python
from django.db import transaction

from rag.exceptions import RagError
from rag.generation.answer_service import answer_question
from rag.schemas import AnswerStatus

from assistant.models import Answer, Citation, Question
from machines.models import Machine, Manual

RAG_TO_DB_STATUS = {
    AnswerStatus.ANSWERED: Question.Status.ANSWERED,
    AnswerStatus.INSUFFICIENT_EVIDENCE: Question.Status.INSUFFICIENT_EVIDENCE,
    # PROVIDER_UNAVAILABLE and GENERATION_ERROR -> Question.Status.FAILED (no Answer row)
}


def ask_question(*, user, machine: Machine, text: str) -> Question:
    """Serializer has already checked: engineer role, machine active, text 1-1000 chars."""
    question = Question.objects.create(user=user, machine=machine, text=text)  # status PENDING

    try:
        result = answer_question(text, machine.machine_id, store=get_store(), llm=get_llm())
    except RagError as exc:  # VectorStoreError, EmbeddingModelError, InvalidMetadataError, ...
        logger.exception("RAG failure for question %s", question.pk)
        return _fail(question, f"{type(exc).__name__}: {exc}")

    db_status = RAG_TO_DB_STATUS.get(result.status)
    if db_status is None:  # provider_unavailable / generation_error
        return _fail(question, result.error or result.status.value)

    try:
        with transaction.atomic():
            answer = Answer.objects.create(
                question=question,
                text=result.answer,
                status=db_status,
                llm_model=result.llm_model or "",
                retrieved_count=result.retrieval.retrieved_count,
            )
            save_citations(answer, machine, result.citations)
            question.status = db_status
            question.save(update_fields=["status"])
    except Manual.DoesNotExist:  # vector index and DB out of sync
        logger.exception("Cited manual missing for question %s", question.pk)
        return _fail(question, "cited manual revision not found in database")
    return question


def _fail(question: Question, message: str) -> Question:
    question.status = Question.Status.FAILED
    question.error_message = message[:2000]  # diagnostics only; never shown to engineers
    question.save(update_fields=["status", "error_message"])
    return question
```

View mapping (API_CONTRACT §4): `ANSWERED` / `INSUFFICIENT_EVIDENCE` → 201 with answer + citations;
`FAILED` → 503 if `error_message == "provider_unavailable"`, otherwise 502.

## 3. Converting RAG citations into rows

```python
def save_citations(answer: Answer, machine: Machine, citations) -> None:
    """citations: tuple[rag.schemas.Citation, ...] — every field comes from the retrieved record."""
    for rank, c in enumerate(citations, start=1):
        # Filtering by machine also enforces "citation belongs to the selected machine".
        manual = Manual.objects.get(machine=machine, document_id=c.document_id, revision=c.revision)
        Citation.objects.create(
            answer=answer,
            manual=manual,
            pdf_page=c.pdf_page,       # original one-based PDF page
            passage=c.passage,         # snapshot of the chunk text
            chunk_id=c.chunk_id,
            source_tag=c.source_tag,   # matches the [S#] markers in answer.text
            rank=rank,
        )
```

API serializer fields not stored on `Citation`: `document_id`, `document_name`, `revision` come from
`citation.manual`; `source_url` = `f"/api/manuals/{citation.manual_id}/file/#page={citation.pdf_page}"`.

## 4. Indexing a newly uploaded manual

```python
from django.utils import timezone

from rag.exceptions import RagError
from rag.ingestion.chunker import chunk_document
from rag.ingestion.pdf_extractor import extract_pdf

from machines.models import IngestionJob, Manual


def index_uploaded_manual(manual: Manual) -> Manual:
    """Called after the upload view saved the file and Manual(status=PROCESSING, is_current=False)."""
    job = IngestionJob.objects.create(manual=manual, status="RUNNING")
    machine_id = manual.machine.machine_id
    store = get_store()

    try:
        document = extract_pdf(
            manual.file.path,
            machine_id=machine_id,
            document_id=manual.document_id,
            revision=manual.revision,
            source_filename=manual.original_filename,  # shown in citations, not the stored file name
        )
        with _write_lock:
            summary = store.index_manual(chunk_document(document), make_current=False)  # staged
    except RagError as exc:  # InvalidPdfError, PdfExtractionError, InvalidChunkError, EmbeddingModelError, VectorStoreError
        with _write_lock:
            store.remove_manual(machine_id=machine_id, document_id=manual.document_id, revision=manual.revision)
        manual.status = Manual.Status.FAILED
        manual.save(update_fields=["status"])
        job.status, job.error_message, job.completed_at = "FAILED", f"{type(exc).__name__}: {exc}", timezone.now()
        job.save()
        return manual

    with transaction.atomic():
        Manual.objects.filter(machine=manual.machine, document_id=manual.document_id).exclude(pk=manual.pk) \
            .update(is_current=False)
        manual.status, manual.page_count, manual.is_current = Manual.Status.INDEXED, document.page_count, True
        manual.save(update_fields=["status", "page_count", "is_current"])
        job.status = "SUCCEEDED"
        job.pages_processed = document.page_count
        job.chunks_indexed = summary.chunks_indexed
        job.embedding_model = summary.embedding_model
        job.completed_at = timezone.now()
        job.save()

    with _write_lock:
        store.set_current_revision(machine_id=machine_id, document_id=manual.document_id, revision=manual.revision)
    return manual
```

If `set_current_revision` fails after the commit, retrieval keeps using the previous revision; log it and let
the admin retry (re-running this function for the same revision is safe — indexing is idempotent).

## 5. Switching the current revision

```python
from rag.exceptions import RevisionNotFoundError


def make_revision_current(manual: Manual) -> None:
    if manual.status != Manual.Status.INDEXED:
        raise ValueError("Only an indexed revision can become current")
    with transaction.atomic():
        Manual.objects.filter(machine=manual.machine, document_id=manual.document_id).update(is_current=False)
        Manual.objects.filter(pk=manual.pk).update(is_current=True)
    try:
        with _write_lock:
            get_store().set_current_revision(
                machine_id=manual.machine.machine_id, document_id=manual.document_id, revision=manual.revision
            )
    except RevisionNotFoundError:
        # DB says indexed but the vector index has no records: re-index it.
        index_uploaded_manual(manual)
```

## 6. Removing a revision from the index

```python
def remove_revision_from_index(manual: Manual) -> int:
    with _write_lock:
        removed = get_store().remove_manual(
            machine_id=manual.machine.machine_id, document_id=manual.document_id, revision=manual.revision
        )
    Manual.objects.filter(pk=manual.pk).update(is_current=False)
    return removed
```

The `Manual` row and file are kept (cited manuals are `PROTECT`ed). The current DB design has no status for
"stored but removed from the index"; add one (e.g. `ARCHIVED`) if this action is exposed. It is not required
by the challenge brief.

## 7. Handling RAG exceptions in views

| Exception (`rag.exceptions`) | Typical source | Response |
|---|---|---|
| `InvalidPdfError` | upload is not a readable PDF | 400 `{"file": ["..."]}` (or 201 with `status: FAILED` per API_CONTRACT) |
| `InvalidChunkError` | `document_id` already used by another machine | 400 `non_field_errors` |
| `InvalidMetadataError` | blank question / machine reached the RAG layer | 400 (should be caught by the serializer first) |
| `RevisionNotFoundError` | switching to a revision with no indexed records | 400/404 to admin |
| `VectorStoreError`, `EmbeddingModelError` | index or model unavailable | 502, record marked `FAILED` |
| `ConfigError` | bad `LLM_PROVIDER` / RAG settings | fail at startup; fix environment |

## 8. Django tests without loading the model

Patch the service-level getters so Django tests stay fast and offline:

```python
from unittest import mock

from rag.config import VectorStoreConfig
from rag.tests.support import HashingEmbedder, ScriptedLLM, answer_json  # test-only helpers
from rag.vectorstore.chroma_store import ChromaVectorStore


class AskQuestionTests(TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        store = ChromaVectorStore(HashingEmbedder(), VectorStoreConfig(Path(tmp), "django_test_chunks"))
        self.enterContext(mock.patch("assistant.rag_services.get_store", return_value=store))
        self.enterContext(mock.patch("assistant.rag_services.get_llm", return_value=ScriptedLLM(...)))
```

The challenge's mandatory check "retrieval excludes passages belonging to another machine" already runs in
`rag/tests/` (`test_chroma_store.py`, `test_retrieval_integration.py`); the Django suite owns "Engineers cannot
upload manuals".
