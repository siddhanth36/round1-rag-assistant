"""End-to-end retrieval with the real model: real PDFs -> chunks -> MiniLM -> ChromaDB.

Uses a temporary ChromaDB directory. Skipped (never silently passed) if the embedding
model cannot be loaded; see test_embedder.py for model download notes.
Expected sources come from reading the supplied manuals.
"""

from __future__ import annotations

import pytest

from rag.config import EmbeddingConfig, VectorStoreConfig
from rag.embeddings.embedder import get_embedder
from rag.exceptions import EmbeddingModelError
from rag.vectorstore.chroma_store import ChromaVectorStore

HP, AC = "M-HP200", "M-AC90"

# See rag/vectorstore/chroma_store.py: ChromaDB 1.5 warns about embedding_function=None.
pytestmark = pytest.mark.filterwarnings("ignore:legacy embedding function config:DeprecationWarning")


@pytest.fixture(scope="module")
def real_store(tmp_path_factory, manual_chunks) -> ChromaVectorStore:
    try:
        embedder = get_embedder(EmbeddingConfig())
    except EmbeddingModelError as exc:
        pytest.skip(f"Embedding model unavailable: {exc}")
    store = ChromaVectorStore(embedder, VectorStoreConfig(tmp_path_factory.mktemp("chroma"), "integration_chunks"))
    for chunks in manual_chunks.values():
        store.index_manual(chunks)
    return store


def _ids(results) -> list[str]:
    return [r.chunk_id for r in results]


def test_e17_on_hp200_retrieves_hp200_low_pressure_evidence(real_store: ChromaVectorStore) -> None:
    results = real_store.search("What does E17 mean?", machine_id=HP, limit=4)
    assert {r.machine_id for r in results} == {HP}
    assert any("110 bar" in r.text and "E17" in r.text for r in results), _ids(results)


def test_e17_on_ac90_retrieves_ac90_alarm_table(real_store: ChromaVectorStore) -> None:
    results = real_store.search("What does E17 mean?", machine_id=AC, limit=4)
    assert {r.machine_id for r in results} == {AC}
    top = results[0]
    assert (top.document_id, top.pdf_page) == ("AC-OM-01", 2)
    assert "High discharge temperature" in top.text


def test_af10_question_ranks_ac90_schedule_first(real_store: ChromaVectorStore) -> None:
    results = real_store.search(
        "How often should the AF-10 filter indicator be checked, and when is planned replacement due?",
        machine_id=AC,
        limit=4,
    )
    assert (results[0].document_id, results[0].pdf_page) == ("AC-OM-01", 3)
    assert "Every 600 operating hours" in results[0].text


def test_hp200_multi_document_question_returns_both_manuals(real_store: ChromaVectorStore) -> None:
    results = real_store.search("What triggers E17 and what should be checked first?", machine_id=HP, limit=4)
    assert {r.document_id for r in results} == {"HP-OPS-01", "HP-MNT-01"}


def test_hp200_filter_question_returns_rf20_schedule(real_store: ChromaVectorStore) -> None:
    results = real_store.search("How often is the filter checked?", machine_id=HP, limit=3)
    assert ("HP-MNT-01", 2) in {(r.document_id, r.pdf_page) for r in results}


@pytest.mark.parametrize("machine, foreign_doc", [(AC, "HP-OPS-01"), (AC, "HP-MNT-01"), (HP, "AC-OM-01")])
def test_verbatim_other_machine_text_stays_isolated(real_store, manual_chunks, machine, foreign_doc) -> None:
    for chunk in manual_chunks[foreign_doc]:
        results = real_store.search(chunk.text, machine_id=machine, limit=50)
        assert results and {r.machine_id for r in results} == {machine}


def test_distances_are_ordered_and_reported_raw(real_store: ChromaVectorStore) -> None:
    results = real_store.search("hydraulic oil replacement interval", machine_id=HP, limit=5)
    distances = [r.cosine_distance for r in results]
    assert distances == sorted(distances)
    assert all(0.0 <= d <= 2.0 for d in distances)  # cosine distance range
