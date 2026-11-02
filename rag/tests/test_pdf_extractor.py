"""Tests for rag.ingestion.pdf_extractor against the supplied challenge manuals."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from rag.exceptions import InvalidMetadataError, InvalidPdfError
from rag.ingestion.chunker import chunk_document
from rag.ingestion.pdf_extractor import _normalize_block, extract_pdf
from rag.tests.conftest import MANUAL_RECORDS, REPO_ROOT, ManualRecord, record_for

IDS = [r.document_id for r in MANUAL_RECORDS]


def test_csv_lists_the_three_expected_manuals() -> None:
    assert {(r.machine_id, r.document_id, r.revision, r.pdf_filename) for r in MANUAL_RECORDS} == {
        ("M-HP200", "HP-OPS-01", "1.0", "HP200_Operations_Rev1_0.pdf"),
        ("M-HP200", "HP-MNT-01", "1.0", "HP200_Maintenance_Rev1_0.pdf"),
        ("M-AC90", "AC-OM-01", "1.0", "AC90_Operations_Maintenance_Rev1_0.pdf"),
    }


@pytest.mark.parametrize("record", MANUAL_RECORDS, ids=IDS)
def test_extracts_every_manual_with_three_pages(record: ManualRecord, extracted_manuals) -> None:
    doc = extracted_manuals[record.document_id]
    assert doc.page_count == 3
    assert len(doc.pages) == 3
    assert all(not page.is_blank for page in doc.pages)


@pytest.mark.parametrize("record", MANUAL_RECORDS, ids=IDS)
def test_page_numbers_are_one_based_and_sequential(record: ManualRecord, extracted_manuals) -> None:
    assert [p.pdf_page for p in extracted_manuals[record.document_id].pages] == [1, 2, 3]


@pytest.mark.parametrize("record", MANUAL_RECORDS, ids=IDS)
def test_metadata_matches_csv_on_document_and_every_page(record: ManualRecord, extracted_manuals) -> None:
    doc = extracted_manuals[record.document_id]
    expected = (record.machine_id, record.document_id, record.revision, record.pdf_filename)
    assert (doc.machine_id, doc.document_id, doc.revision, doc.source_filename) == expected
    for page in doc.pages:
        assert (page.machine_id, page.document_id, page.revision, page.source_filename) == expected


@pytest.mark.parametrize("record", MANUAL_RECORDS, ids=IDS)
def test_pages_are_extracted_separately(record: ManualRecord, extracted_manuals) -> None:
    """Each manual prints a footer '<DOC> | Rev <rev> | Page <n>'; it must appear only on PDF page n.

    This checks the supplied manuals (whose printed labels match PDF pages per the brief);
    the extractor itself never reads printed labels.
    """
    pages = extracted_manuals[record.document_id].pages
    for page in pages:
        footer = f"{record.document_id} | Rev {record.revision} | Page {page.pdf_page}"
        assert footer in page.page_text
        assert all(footer not in other.page_text for other in pages if other is not page)


def test_source_filename_override_is_used() -> None:
    record = record_for("HP-OPS-01")
    doc = extract_pdf(
        record.path,
        machine_id=record.machine_id,
        document_id=record.document_id,
        revision=record.revision,
        source_filename="uploaded-copy.pdf",
    )
    assert doc.source_filename == "uploaded-copy.pdf"
    assert {p.source_filename for p in doc.pages} == {"uploaded-copy.pdf"}


def test_blank_page_is_kept_and_yields_no_chunks(tmp_path: Path) -> None:
    """A real manual page followed by an inserted blank page."""
    record = record_for("AC-OM-01")
    out = tmp_path / "with_blank.pdf"
    with pymupdf.open(record.path) as src, pymupdf.open() as doc:
        doc.insert_pdf(src, from_page=0, to_page=0)
        doc.new_page()
        doc.save(out)

    extracted = extract_pdf(out, machine_id=record.machine_id, document_id=record.document_id, revision="1.0")

    assert [p.pdf_page for p in extracted.pages] == [1, 2]
    assert not extracted.pages[0].is_blank
    assert extracted.pages[1].is_blank and extracted.pages[1].page_text == ""
    chunks = chunk_document(extracted)
    assert chunks and {c.pdf_page for c in chunks} == {1}


def test_fully_blank_pdf_extracts_without_error(tmp_path: Path) -> None:
    out = tmp_path / "blank.pdf"
    with pymupdf.open() as doc:
        doc.new_page()
        doc.save(out)
    extracted = extract_pdf(out, machine_id="M-X", document_id="DOC-X", revision="1.0")
    assert extracted.page_count == 1
    assert extracted.pages[0].is_blank
    assert chunk_document(extracted) == []


def _extract(path: Path) -> None:
    extract_pdf(path, machine_id="M-X", document_id="DOC-X", revision="1.0")


def test_non_pdf_content_raises_invalid_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "not_really.pdf"
    bad.write_text("this is plain text, not a PDF", encoding="utf-8")
    with pytest.raises(InvalidPdfError, match="Cannot open not_really.pdf"):
        _extract(bad)


def test_empty_file_raises_invalid_pdf(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(InvalidPdfError):
        _extract(empty)


def test_missing_file_raises_invalid_pdf(tmp_path: Path) -> None:
    with pytest.raises(InvalidPdfError, match="not found"):
        _extract(tmp_path / "missing.pdf")


def test_encrypted_pdf_raises_invalid_pdf(tmp_path: Path) -> None:
    record = record_for("HP-MNT-01")
    out = tmp_path / "encrypted.pdf"
    with pymupdf.open(record.path) as doc:
        doc.save(out, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner-test", user_pw="user-test")
    with pytest.raises(InvalidPdfError, match="encrypted"):
        _extract(out)


@pytest.mark.parametrize("field", ["machine_id", "document_id", "revision"])
@pytest.mark.parametrize("bad_value", ["", "   "])
def test_empty_metadata_is_rejected(field: str, bad_value: str) -> None:
    record = record_for("HP-OPS-01")
    kwargs = {"machine_id": record.machine_id, "document_id": record.document_id, "revision": record.revision}
    kwargs[field] = bad_value
    with pytest.raises(InvalidMetadataError, match=field):
        extract_pdf(record.path, **kwargs)


# --- regressions -----------------------------------------------------------------------------


def test_compound_words_wrapped_after_hyphen_are_rejoined(extracted_manuals) -> None:
    """HP-MNT-01 wraps 'stored-energy' (p.1) and '500-hour' (p.2) across lines.

    Regression: lines were joined with a space, producing 'stored- energy' / '500- hour'.
    """
    pages = extracted_manuals["HP-MNT-01"].pages
    assert "stored-energy procedure" in pages[0].page_text
    assert "planned 500-hour interval" in pages[1].page_text
    for doc in extracted_manuals.values():
        for page in doc.pages:
            assert not re.search(r"\w- \w", page.page_text), (doc.document_id, page.pdf_page)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("stored-\nenergy", "stored-energy"),   # wrapped compound: keep hyphen, no space
        ("500-\nhour", "500-hour"),
        ("limit -\nnext", "limit - next"),       # free-standing dash is not a compound
        ("line one\nline two", "line one line two"),
        ("  spaced \t out \n\n next ", "spaced out next"),
    ],
)
def test_block_line_joining(raw: str, expected: str) -> None:
    assert _normalize_block(raw) == expected


def test_extractor_import_emits_no_deprecation_output() -> None:
    """Regression: `import fitz` printed a deprecation warning on PyMuPDF 1.28."""
    result = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-c", "import rag.ingestion.pdf_extractor"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "deprecated" not in (result.stdout + result.stderr).lower()
