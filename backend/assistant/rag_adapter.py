"""The ONLY module that imports rag/. Django calls rag through these three functions.

The vector store (loads the embedding model, ~10 s cold) and the LLM client are created lazily on first use
behind a lock — never in AppConfig.ready(), so migrate/check/tests don't load torch. One Django process
(rag contract §2): run `runserver --nothreading` locally.
"""

import threading
from pathlib import Path

from rag.embeddings.embedder import get_embedder
from rag.exceptions import RagError
from rag.generation.answer_service import INSUFFICIENT_EVIDENCE_MESSAGE, answer_question
from rag.generation.llm_client import create_llm_client
from rag.ingestion.chunker import chunk_document
from rag.ingestion.pdf_extractor import extract_pdf
from rag.vectorstore.chroma_store import ChromaVectorStore

__all__ = ["INSUFFICIENT_EVIDENCE_MESSAGE", "ask", "index_manual"]

_init_lock = threading.Lock()
_write_lock = threading.Lock()  # rag's write thread-safety is untested: serialize index/remove (contract §2)
_store = None
_llm = None
_llm_loaded = False


def get_store() -> ChromaVectorStore:
    global _store
    if _store is None:
        with _init_lock:
            if _store is None:
                _store = ChromaVectorStore(get_embedder())  # CHROMA_DIR / EMBEDDING_* from the environment
    return _store


def get_llm():
    """LLMClient, or None when LLM_PROVIDER is empty. Raises rag ConfigError for a bad provider config."""
    global _llm, _llm_loaded
    if not _llm_loaded:
        with _init_lock:
            if not _llm_loaded:
                _llm = create_llm_client()
                _llm_loaded = True
    return _llm


def ask(question: str, machine_id: str):
    """rag.answer_question for one machine. Returns rag's AnswerResult; RagError propagates."""
    return answer_question(question, machine_id, store=get_store(), llm=get_llm())


def index_manual(pdf_path: str | Path, *, machine_id: str, document_id: str, revision: str, source_filename: str):
    """extract -> chunk -> index_manual(make_current=True). Returns (page_count, IndexSummary); RagError propagates.

    On failure the partial records of this revision are removed so nothing half-indexed is ever retrievable.
    """
    document = extract_pdf(pdf_path, machine_id=machine_id, document_id=document_id, revision=revision,
                           source_filename=source_filename)
    store = get_store()
    with _write_lock:
        try:
            # ponytail: make_current=True in one call (contract §8 allows it); switch to the staged
            # make_current=False -> DB commit -> set_current_revision flow when revisions get an is_current column.
            summary = store.index_manual(chunk_document(document), make_current=True)
        except RagError:
            store.remove_manual(machine_id=machine_id, document_id=document_id, revision=revision)
            raise
    return document.page_count, summary
