"""Tests for rag.vectorstore.chroma_store using the real manuals' chunks.

Every test uses its own temporary ChromaDB directory (never the project's chroma_db/).
Embeddings come from the offline HashingEmbedder (rag/tests/support.py) so these tests
exercise storage, isolation and revision logic without downloading a model.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from rag.config import VectorStoreConfig, load_vector_store_config
from rag.exceptions import (
    InvalidChunkError,
    InvalidMetadataError,
    RevisionNotFoundError,
    VectorStoreError,
)
from rag.ingestion.chunker import chunk_document, make_chunk_id
from rag.schemas import Chunk
from rag.tests.conftest import REPO_ROOT
from rag.tests.support import HashingEmbedder
from rag.vectorstore.chroma_store import ChromaVectorStore

HP, AC = "M-HP200", "M-AC90"

# See rag/vectorstore/chroma_store.py: ChromaDB 1.5 warns about embedding_function=None.
pytestmark = pytest.mark.filterwarnings("ignore:legacy embedding function config:DeprecationWarning")


def _store(path: Path, embedder: HashingEmbedder | None = None, name: str = "test_chunks") -> ChromaVectorStore:
    return ChromaVectorStore(embedder or HashingEmbedder(), VectorStoreConfig(persist_dir=path, collection_name=name))


def _relabel(chunks: list[Chunk], revision: str) -> list[Chunk]:
    """Same real text under another revision label — used only to test revision mechanics."""
    return [
        replace(c, revision=revision, chunk_id=make_chunk_id(c.document_id, revision, c.pdf_page, c.chunk_index))
        for c in chunks
    ]


def _all_records(store: ChromaVectorStore) -> dict:
    return store._collection.get(include=["metadatas", "documents"])


def _current_revisions(store: ChromaVectorStore, document_id: str) -> set[str]:
    meta = store._collection.get(where={"document_id": document_id}, include=["metadatas"])["metadatas"]
    return {m["revision"] for m in meta if m["is_current"]}


@pytest.fixture()
def store(tmp_path: Path) -> ChromaVectorStore:
    return _store(tmp_path / "chroma")


@pytest.fixture()
def indexed_store(store: ChromaVectorStore, manual_chunks) -> ChromaVectorStore:
    for chunks in manual_chunks.values():
        store.index_manual(chunks)
    return store


def _total(manual_chunks) -> int:
    return sum(len(c) for c in manual_chunks.values())


# --- initialization & persistence ------------------------------------------------------------


def test_collection_initializes_empty_with_cosine_space(tmp_path: Path) -> None:
    store = _store(tmp_path / "chroma")
    assert store.count() == 0
    assert (tmp_path / "chroma").is_dir()
    assert store._collection.configuration["hnsw"]["space"] == "cosine"
    assert store._collection.metadata["embedding_dimension"] == 384


def test_default_embedding_function_is_not_used(store: ChromaVectorStore) -> None:
    assert store._collection._embedding_function is None


def test_records_can_be_added_and_retrieved(store: ChromaVectorStore, manual_chunks) -> None:
    chunks = manual_chunks["HP-OPS-01"]
    summary = store.index_manual(chunks)
    assert summary.chunks_indexed == len(chunks) == store.count()
    assert summary.pages_indexed == (1, 2, 3)
    assert summary.is_current is True
    results = store.search(chunks[0].text, machine_id=HP, limit=3)
    assert results[0].chunk_id == chunks[0].chunk_id


def test_records_persist_after_reopening_in_new_process(tmp_path: Path, manual_chunks) -> None:
    path = tmp_path / "chroma"
    _store(path).index_manual(manual_chunks["AC-OM-01"])
    script = (
        "from pathlib import Path\n"
        "from rag.config import VectorStoreConfig\n"
        "from rag.tests.support import HashingEmbedder\n"
        "from rag.vectorstore.chroma_store import ChromaVectorStore\n"
        f"s = ChromaVectorStore(HashingEmbedder(), VectorStoreConfig(Path({str(path)!r}), 'test_chunks'))\n"
        "print(s.count(), s.search('AF-10 inlet filter', machine_id='M-AC90', limit=1)[0].document_id)\n"
    )
    out = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.split() == [str(len(manual_chunks["AC-OM-01"])), "AC-OM-01"]


def test_reopening_with_a_different_model_is_refused(tmp_path: Path, manual_chunks) -> None:
    path = tmp_path / "chroma"
    _store(path).index_manual(manual_chunks["AC-OM-01"])
    with pytest.raises(VectorStoreError, match="was built with model"):
        _store(path, HashingEmbedder(model_name="another-model"))


def test_vector_store_config_reads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("CHROMA_COLLECTION", "my_chunks")
    assert load_vector_store_config() == VectorStoreConfig(tmp_path / "x", "my_chunks")


def test_default_store_location_is_git_ignored() -> None:
    default_dir = VectorStoreConfig().persist_dir
    assert default_dir.parent == REPO_ROOT and default_dir.name == "chroma_db"
    probe = subprocess.run(["git", "check-ignore", "-q", "chroma_db/x"], cwd=REPO_ROOT, check=False)
    assert probe.returncode == 0


# --- indexing ------------------------------------------------------------------------------


def test_reindexing_same_manual_does_not_duplicate(store: ChromaVectorStore, manual_chunks) -> None:
    chunks = manual_chunks["HP-MNT-01"]
    store.index_manual(chunks)
    ids_first = sorted(_all_records(store)["ids"])
    summary = store.index_manual(chunks)
    assert store.count() == len(chunks)
    assert sorted(_all_records(store)["ids"]) == ids_first
    assert summary.stale_records_removed == 0


def test_reindexing_with_new_chunking_removes_stale_records(store, extracted_manuals) -> None:
    from rag.config import ChunkingConfig

    doc = extracted_manuals["HP-OPS-01"]
    small = chunk_document(doc, ChunkingConfig(chunk_size=40, overlap=5))
    default = chunk_document(doc)
    store.index_manual(small)
    summary = store.index_manual(default)
    assert store.count() == len(default)
    assert summary.stale_records_removed == len({c.chunk_id for c in small} - {c.chunk_id for c in default})


def test_embeddings_are_computed_in_one_batch_per_manual(tmp_path: Path, manual_chunks) -> None:
    embedder = HashingEmbedder()
    _store(tmp_path / "chroma", embedder).index_manual(manual_chunks["HP-OPS-01"])
    assert embedder.calls == 1


def test_metadata_and_text_preserved_exactly(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    records = _all_records(indexed_store)
    stored = {i: (m, d) for i, m, d in zip(records["ids"], records["metadatas"], records["documents"], strict=True)}
    for chunks in manual_chunks.values():
        for c in chunks:
            meta, text = stored[c.chunk_id]
            assert text == c.text
            assert meta == {
                "chunk_id": c.chunk_id,
                "chunk_index": c.chunk_index,
                "machine_id": c.machine_id,
                "document_id": c.document_id,
                "revision": c.revision,
                "source_filename": c.source_filename,
                "pdf_page": c.pdf_page,
                "is_current": True,
            }
            assert isinstance(meta["pdf_page"], int) and meta["pdf_page"] >= 1


def test_search_results_carry_source_metadata(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    expected = {c.chunk_id: c for chunks in manual_chunks.values() for c in chunks}
    for machine in (HP, AC):
        for r in indexed_store.search("filter replacement interval", machine_id=machine, limit=5):
            c = expected[r.chunk_id]
            assert (r.machine_id, r.document_id, r.revision, r.pdf_page, r.source_filename, r.text) == (
                c.machine_id, c.document_id, c.revision, c.pdf_page, c.source_filename, c.text
            )
            assert r.cosine_distance >= 0


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda cs: [], "No chunks"),
        (lambda cs: cs[:1] + [replace(cs[1], machine_id="M-AC90")], "mix sources"),
        (lambda cs: cs[:1] + [replace(cs[1], revision="9.9")], "mix sources"),
        (lambda cs: cs[:1] + [replace(cs[1], document_id="AC-OM-01")], "mix sources"),
        (lambda cs: [replace(cs[0], text="   ")], "empty text"),
        (lambda cs: [replace(cs[0], chunk_id="random-id")], "does not match"),
        (lambda cs: [cs[0], cs[0]], "Duplicate"),
    ],
)
def test_invalid_chunk_batches_are_rejected_without_writing(store, manual_chunks, mutate, message) -> None:
    with pytest.raises(InvalidChunkError, match=message):
        store.index_manual(mutate(list(manual_chunks["HP-OPS-01"])))
    assert store.count() == 0


def test_document_cannot_be_indexed_under_a_second_machine(store, manual_chunks) -> None:
    store.index_manual(manual_chunks["HP-OPS-01"])
    hijacked = [replace(c, machine_id=AC) for c in manual_chunks["HP-OPS-01"]]
    with pytest.raises(InvalidChunkError, match="already indexed for machine 'M-HP200'"):
        store.index_manual(hijacked)


# --- machine isolation -----------------------------------------------------------------------


def test_hp200_search_spans_both_hp200_manuals(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    hp_total = len(manual_chunks["HP-OPS-01"]) + len(manual_chunks["HP-MNT-01"])
    results = indexed_store.search("E17 low hydraulic pressure checks", machine_id=HP, limit=hp_total)
    assert len(results) == hp_total
    assert {r.document_id for r in results} == {"HP-OPS-01", "HP-MNT-01"}


@pytest.mark.parametrize("machine, foreign_doc", [(AC, "HP-OPS-01"), (AC, "HP-MNT-01"), (HP, "AC-OM-01")])
def test_verbatim_foreign_text_never_retrieves_other_machine(indexed_store, manual_chunks, machine, foreign_doc) -> None:
    """Query with the exact text of another machine's chunk and ask for every record."""
    for foreign_chunk in manual_chunks[foreign_doc]:
        results = indexed_store.search(foreign_chunk.text, machine_id=machine, limit=50)
        assert results
        assert {r.machine_id for r in results} == {machine}
        assert foreign_chunk.chunk_id not in {r.chunk_id for r in results}


def test_ac90_returns_only_ac_om_01(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    results = indexed_store.search("What does E17 mean?", machine_id=AC, limit=50)
    assert {r.document_id for r in results} == {"AC-OM-01"}
    assert len(results) == len(manual_chunks["AC-OM-01"])


@pytest.mark.parametrize("machine_id", ["", "   ", None])
def test_empty_machine_id_is_rejected(indexed_store: ChromaVectorStore, machine_id) -> None:
    with pytest.raises(InvalidMetadataError, match="machine_id"):
        indexed_store.search("E17", machine_id=machine_id)


def test_unknown_machine_returns_nothing(indexed_store: ChromaVectorStore) -> None:
    assert indexed_store.search("E17", machine_id="M-UNKNOWN") == []


@pytest.mark.parametrize("query, limit", [("", 5), ("  ", 5), ("E17", 0), ("E17", 51)])
def test_invalid_query_or_limit_is_rejected(indexed_store: ChromaVectorStore, query, limit) -> None:
    with pytest.raises(InvalidMetadataError):
        indexed_store.search(query, machine_id=HP, limit=limit)


# --- revisions -------------------------------------------------------------------------------


def test_new_current_revision_hides_old_revision(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    indexed_store.index_manual(_relabel(manual_chunks["HP-OPS-01"], "1.1"))
    assert _current_revisions(indexed_store, "HP-OPS-01") == {"1.1"}
    results = indexed_store.search("normal hydraulic pressure", machine_id=HP, limit=50)
    assert {(r.document_id, r.revision) for r in results} == {("HP-OPS-01", "1.1"), ("HP-MNT-01", "1.0")}


def test_unpromoted_revision_is_stored_but_not_retrieved(indexed_store, manual_chunks) -> None:
    summary = indexed_store.index_manual(_relabel(manual_chunks["HP-OPS-01"], "1.1"), make_current=False)
    assert summary.is_current is False
    assert _current_revisions(indexed_store, "HP-OPS-01") == {"1.0"}
    results = indexed_store.search("normal hydraulic pressure", machine_id=HP, limit=50)
    assert all(r.revision == "1.0" for r in results)


def test_reindexing_current_revision_keeps_it_current(indexed_store, manual_chunks) -> None:
    summary = indexed_store.index_manual(manual_chunks["HP-OPS-01"], make_current=False)
    assert summary.is_current is True
    assert _current_revisions(indexed_store, "HP-OPS-01") == {"1.0"}


def test_set_current_revision_switches_back_and_forth(indexed_store, manual_chunks) -> None:
    indexed_store.index_manual(_relabel(manual_chunks["HP-OPS-01"], "1.1"))
    changed = indexed_store.set_current_revision(machine_id=HP, document_id="HP-OPS-01", revision="1.0")
    assert changed == 2 * len(manual_chunks["HP-OPS-01"])
    assert _current_revisions(indexed_store, "HP-OPS-01") == {"1.0"}
    assert indexed_store.set_current_revision(machine_id=HP, document_id="HP-OPS-01", revision="1.0") == 0


def test_revision_change_does_not_affect_other_documents_or_machines(indexed_store, manual_chunks) -> None:
    before = {i: m["is_current"] for i, m in zip(*(_all_records(indexed_store)[k] for k in ("ids", "metadatas")))}
    indexed_store.index_manual(_relabel(manual_chunks["HP-OPS-01"], "1.1"))
    after = {i: m["is_current"] for i, m in zip(*(_all_records(indexed_store)[k] for k in ("ids", "metadatas")))}
    for chunk_id, was_current in before.items():
        if not chunk_id.startswith("HP-OPS-01:"):
            assert after[chunk_id] == was_current, chunk_id
    assert _current_revisions(indexed_store, "HP-MNT-01") == {"1.0"}
    assert _current_revisions(indexed_store, "AC-OM-01") == {"1.0"}


@pytest.mark.parametrize(
    "machine_id, document_id, revision",
    [(HP, "HP-OPS-01", "2.0"), (AC, "HP-OPS-01", "1.0"), (HP, "NO-SUCH-DOC", "1.0")],
)
def test_set_current_revision_unknown_target_raises_and_changes_nothing(
    indexed_store, machine_id, document_id, revision
) -> None:
    before = _all_records(indexed_store)["metadatas"]
    with pytest.raises(RevisionNotFoundError):
        indexed_store.set_current_revision(machine_id=machine_id, document_id=document_id, revision=revision)
    assert _all_records(indexed_store)["metadatas"] == before


# --- removal ---------------------------------------------------------------------------------


def test_removing_one_manual_keeps_the_others(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    removed = indexed_store.remove_manual(machine_id=HP, document_id="HP-OPS-01", revision="1.0")
    assert removed == len(manual_chunks["HP-OPS-01"])
    remaining = {m["document_id"] for m in _all_records(indexed_store)["metadatas"]}
    assert remaining == {"HP-MNT-01", "AC-OM-01"}
    assert indexed_store.count() == len(manual_chunks["HP-MNT-01"]) + len(manual_chunks["AC-OM-01"])


def test_remove_with_wrong_machine_deletes_nothing(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    assert indexed_store.remove_manual(machine_id=AC, document_id="HP-OPS-01", revision=None) == 0
    assert indexed_store.count() == _total(manual_chunks)


def test_remove_all_revisions_of_one_document(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    indexed_store.index_manual(_relabel(manual_chunks["HP-OPS-01"], "1.1"), make_current=False)
    removed = indexed_store.remove_manual(machine_id=HP, document_id="HP-OPS-01", revision=None)
    assert removed == 2 * len(manual_chunks["HP-OPS-01"])
    assert indexed_store.count() == len(manual_chunks["HP-MNT-01"]) + len(manual_chunks["AC-OM-01"])


def test_remove_requires_non_empty_identifiers(indexed_store: ChromaVectorStore, manual_chunks) -> None:
    with pytest.raises(InvalidMetadataError):
        indexed_store.remove_manual(machine_id="", document_id="HP-OPS-01", revision=None)
    with pytest.raises(InvalidMetadataError):
        indexed_store.remove_manual(machine_id=HP, document_id=" ", revision="1.0")
    assert indexed_store.count() == _total(manual_chunks)
