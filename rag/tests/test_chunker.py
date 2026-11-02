"""Tests for rag.ingestion.chunker, using text extracted from the supplied manuals."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from rag.config import ChunkingConfig, load_chunking_config
from rag.exceptions import ChunkingConfigError
from rag.ingestion.chunker import chunk_document, chunk_page, make_chunk_id
from rag.schemas import Chunk, ExtractedDocument, ExtractedPage
from rag.tests.conftest import MANUAL_RECORDS, record_for

SMALL = ChunkingConfig(chunk_size=40, overlap=8)


def _words(text: str) -> list[str]:
    return text.split()


def _page_chunks(chunks: list[Chunk], pdf_page: int) -> list[Chunk]:
    return [c for c in chunks if c.pdf_page == pdf_page]


# --- metadata preservation (one test per supplied manual) --------------------------------


@pytest.mark.parametrize(
    "document_id, machine_id",
    [("HP-OPS-01", "M-HP200"), ("HP-MNT-01", "M-HP200"), ("AC-OM-01", "M-AC90")],
)
def test_chunks_retain_source_metadata(document_id: str, machine_id: str, extracted_manuals) -> None:
    record = record_for(document_id)
    assert record.machine_id == machine_id  # cross-check against the challenge CSV
    chunks = chunk_document(extracted_manuals[document_id])
    assert chunks
    for chunk in chunks:
        assert chunk.machine_id == record.machine_id
        assert chunk.document_id == record.document_id
        assert chunk.revision == record.revision
        assert chunk.source_filename == record.pdf_filename


# --- page numbers & no mixing --------------------------------------------------------------


def test_every_chunk_has_valid_original_page_and_text_from_that_page(extracted_manuals) -> None:
    for doc in extracted_manuals.values():
        page_text = {p.pdf_page: " ".join(_words(p.page_text)) for p in doc.pages}
        for chunk in chunk_document(doc, SMALL):
            assert isinstance(chunk.pdf_page, int)
            assert 1 <= chunk.pdf_page <= doc.page_count
            assert " ".join(_words(chunk.text)) in page_text[chunk.pdf_page]


def test_no_chunk_mixes_machines_documents_or_revisions(extracted_manuals) -> None:
    all_chunks = [c for doc in extracted_manuals.values() for c in chunk_document(doc, SMALL)]
    identities_by_doc: dict[str, set[tuple[str, str, str]]] = {}
    for chunk in all_chunks:
        identities_by_doc.setdefault(chunk.document_id, set()).add(
            (chunk.machine_id, chunk.revision, chunk.source_filename)
        )
        assert chunk.chunk_id.startswith(f"{chunk.document_id}:{chunk.revision}:p{chunk.pdf_page}:")
    assert all(len(ids) == 1 for ids in identities_by_doc.values())
    assert set(identities_by_doc) == {r.document_id for r in MANUAL_RECORDS}


def test_document_text_never_appears_in_another_machines_chunks(extracted_manuals) -> None:
    """The document ID printed on every page lands only in that document's chunks."""
    all_chunks = [c for doc in extracted_manuals.values() for c in chunk_document(doc)]
    for record in MANUAL_RECORDS:
        for chunk in all_chunks:
            if f"Document ID: {record.document_id}" in chunk.text:
                assert chunk.document_id == record.document_id
                assert chunk.machine_id == record.machine_id


# --- sizing ----------------------------------------------------------------------------------


def test_long_pages_split_into_multiple_bounded_chunks(extracted_manuals) -> None:
    for doc in extracted_manuals.values():
        chunks = chunk_document(doc, SMALL)
        for page in doc.pages:
            on_page = _page_chunks(chunks, page.pdf_page)
            assert len(_words(page.page_text)) > SMALL.chunk_size  # precondition: real page is long
            assert len(on_page) > 1
            assert all(len(_words(c.text)) <= SMALL.chunk_size for c in on_page)


def test_short_page_becomes_one_chunk_with_all_text(extracted_manuals) -> None:
    roomy = ChunkingConfig(chunk_size=5000, overlap=10)
    for doc in extracted_manuals.values():
        chunks = chunk_document(doc, roomy)
        assert len(chunks) == doc.page_count
        for page, chunk in zip(doc.pages, chunks, strict=True):
            assert chunk.chunk_index == 0
            assert _words(chunk.text) == _words(page.page_text)


def test_short_real_paragraph_is_a_single_valid_chunk(extracted_manuals) -> None:
    page = extracted_manuals["HP-OPS-01"].pages[0]
    first_paragraph = page.page_text.split("\n\n")[0]
    chunks = chunk_page(replace(page, page_text=first_paragraph))
    assert len(chunks) == 1
    assert chunks[0].text == first_paragraph
    assert chunks[0].chunk_id == "HP-OPS-01:1.0:p1:c0"


def test_no_empty_chunks(extracted_manuals) -> None:
    for config in (SMALL, ChunkingConfig(chunk_size=5, overlap=4), ChunkingConfig()):
        for doc in extracted_manuals.values():
            assert all(c.text.strip() for c in chunk_document(doc, config))


def test_blank_page_produces_no_chunks() -> None:
    page = ExtractedPage("M-X", "DOC-X", "1.0", "x.pdf", 1, "  \n\n  ")
    assert chunk_page(page) == []


# --- overlap ---------------------------------------------------------------------------------


@pytest.mark.parametrize("overlap", [0, 1, 8, 20])
def test_consecutive_chunks_share_exactly_the_configured_overlap(overlap: int, extracted_manuals) -> None:
    config = ChunkingConfig(chunk_size=40, overlap=overlap)
    for doc in extracted_manuals.values():
        chunks = chunk_document(doc, config)
        for page in doc.pages:
            on_page = _page_chunks(chunks, page.pdf_page)
            rebuilt = _words(on_page[0].text)
            for prev, nxt in zip(on_page, on_page[1:]):
                prev_w, next_w = _words(prev.text), _words(nxt.text)
                if overlap:
                    assert prev_w[-overlap:] == next_w[:overlap]
                rebuilt += next_w[overlap:]
            assert rebuilt == _words(page.page_text)  # nothing lost, nothing duplicated beyond overlap


def test_chunks_prefer_paragraph_boundaries(extracted_manuals) -> None:
    """With room to spare, a non-final chunk ends where a paragraph ends."""
    config = ChunkingConfig(chunk_size=120, overlap=0)
    checked_pages = 0
    for doc in extracted_manuals.values():
        for page in doc.pages:
            paragraphs = page.page_text.split("\n\n")
            if max(len(_words(p)) for p in paragraphs) > config.chunk_size // 2:
                continue
            checked_pages += 1
            chunks = chunk_page(page, config)
            for chunk in chunks:
                # With zero overlap and snapping, each chunk is a run of whole paragraphs.
                assert all(part in paragraphs for part in chunk.text.split("\n\n"))
            for chunk in chunks[:-1]:
                assert chunk.text.count("\n\n") >= 1  # paragraph breaks preserved inside chunk
    assert checked_pages > 0


# --- determinism -----------------------------------------------------------------------------


def test_chunk_ids_are_deterministic_and_unique(extracted_manuals) -> None:
    first = [c for doc in extracted_manuals.values() for c in chunk_document(doc, SMALL)]
    second = [c for doc in extracted_manuals.values() for c in chunk_document(doc, SMALL)]
    assert first == second
    ids = [c.chunk_id for c in first]
    assert len(ids) == len(set(ids))
    for chunk in first:
        assert chunk.chunk_id == make_chunk_id(chunk.document_id, chunk.revision, chunk.pdf_page, chunk.chunk_index)


def test_chunk_index_restarts_per_page(extracted_manuals) -> None:
    chunks = chunk_document(extracted_manuals["HP-MNT-01"], SMALL)
    for page_no in (1, 2, 3):
        assert [c.chunk_index for c in _page_chunks(chunks, page_no)] == list(
            range(len(_page_chunks(chunks, page_no)))
        )


def test_chunking_a_document_preserves_page_order(extracted_manuals) -> None:
    doc: ExtractedDocument = extracted_manuals["AC-OM-01"]
    pages = [c.pdf_page for c in chunk_document(doc, SMALL)]
    assert pages == sorted(pages)


# --- configuration ---------------------------------------------------------------------------


@pytest.mark.parametrize("size, overlap", [(0, 0), (10, -1), (10, 10), (10, 11)])
def test_invalid_chunking_config_is_rejected(size: int, overlap: int) -> None:
    with pytest.raises(ChunkingConfigError):
        ChunkingConfig(chunk_size=size, overlap=overlap)


def test_chunking_config_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_CHUNK_SIZE_WORDS", "64")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP_WORDS", "12")
    assert load_chunking_config() == ChunkingConfig(chunk_size=64, overlap=12)
    monkeypatch.setenv("RAG_CHUNK_SIZE_WORDS", "lots")
    with pytest.raises(ChunkingConfigError):
        load_chunking_config()


# --- architecture guard ----------------------------------------------------------------------


def test_rag_package_does_not_import_django() -> None:
    rag_root = Path(__file__).resolve().parents[1]
    for source in rag_root.rglob("*.py"):
        if source.parent.name == "tests":
            continue
        text = source.read_text(encoding="utf-8")
        assert "import django" not in text and "from django" not in text, source
        assert "rest_framework" not in text, source
