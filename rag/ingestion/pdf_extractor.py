"""Page-level PDF text extraction with PyMuPDF.

Each page is extracted separately and tagged with its original one-based PDF page
number. Pages are never merged, and a page that fails to extract raises an error
instead of being skipped.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pymupdf

from rag.exceptions import InvalidMetadataError, InvalidPdfError, PdfExtractionError
from rag.schemas import ExtractedDocument, ExtractedPage, SourceIdentity

logger = logging.getLogger(__name__)

_WHITESPACE_RUN = re.compile(r"[ \t\r\f\v]+")


def extract_pdf(
    pdf_path: str | Path,
    *,
    machine_id: str,
    document_id: str,
    revision: str,
    source_filename: str | None = None,
) -> ExtractedDocument:
    """Extract the text of every page of a PDF, preserving one-based page numbers.

    Args:
        pdf_path: Path to the PDF on disk.
        machine_id: Owning machine, e.g. ``"M-HP200"``. Supplied by the caller.
        document_id: Manual identifier, e.g. ``"HP-OPS-01"``.
        revision: Manual revision label, e.g. ``"1.0"``.
        source_filename: Original filename to cite. Defaults to the file's name on disk
            (an uploaded file may be stored under a different name than it was uploaded with).

    Returns:
        ExtractedDocument whose ``pages`` has exactly one entry per PDF page, in order.
        Blank pages are kept with empty ``page_text``.

    Raises:
        InvalidMetadataError: A metadata argument is empty.
        InvalidPdfError: The file is missing, not a PDF, corrupted, encrypted, or has no pages.
        PdfExtractionError: Text extraction failed on a specific page.
    """
    path = Path(pdf_path)
    identity = _validate_identity(
        machine_id=machine_id,
        document_id=document_id,
        revision=revision,
        source_filename=source_filename if source_filename is not None else path.name,
    )

    if not path.is_file():
        raise InvalidPdfError(f"PDF file not found: {path}")

    try:
        doc = pymupdf.open(path, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several types (FileDataError, RuntimeError, ...)
        raise InvalidPdfError(f"Cannot open {path.name} as a PDF: {exc}") from exc

    with doc:
        if doc.needs_pass:
            raise InvalidPdfError(f"{path.name} is encrypted and cannot be read")
        if doc.page_count < 1:
            raise InvalidPdfError(f"{path.name} contains no pages")

        pages = tuple(
            ExtractedPage(
                machine_id=identity.machine_id,
                document_id=identity.document_id,
                revision=identity.revision,
                source_filename=identity.source_filename,
                pdf_page=page_number,
                page_text=_extract_page_text(page, page_number, path.name),
            )
            for page_number, page in enumerate(doc, start=1)
        )

    blank = [p.pdf_page for p in pages if p.is_blank]
    if blank:
        logger.warning("%s: no extractable text on page(s) %s", identity.source_filename, blank)

    return ExtractedDocument(
        machine_id=identity.machine_id,
        document_id=identity.document_id,
        revision=identity.revision,
        source_filename=identity.source_filename,
        page_count=len(pages),
        pages=pages,
    )


def _extract_page_text(page: pymupdf.Page, page_number: int, filename: str) -> str:
    """Return a page's text as paragraphs separated by blank lines.

    PyMuPDF text blocks (in reading order) approximate paragraphs / table cells; lines
    inside a block are joined with single spaces so wrapped sentences stay intact, except
    that a compound word hyphenated across a line break is rejoined without a space.
    Image blocks (type 1) are ignored.
    """
    try:
        blocks = page.get_text("blocks", sort=True)
    except Exception as exc:
        raise PdfExtractionError(
            f"Text extraction failed for {filename} page {page_number}: {exc}",
            pdf_page=page_number,
        ) from exc

    paragraphs: list[str] = []
    for block in blocks:
        block_type = block[6]
        if block_type != 0:
            continue
        text = _normalize_block(block[4])
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _normalize_block(raw: str) -> str:
    lines = [_WHITESPACE_RUN.sub(" ", line).strip() for line in raw.splitlines()]
    lines = [line for line in lines if line]
    parts: list[str] = []
    for i, line in enumerate(lines):
        parts.append(line)
        if i < len(lines) - 1:
            parts.append("" if _ends_with_wrapped_hyphen(line, lines[i + 1]) else " ")
    return "".join(parts)


def _ends_with_wrapped_hyphen(line: str, next_line: str) -> bool:
    """True for a compound word wrapped after its hyphen, e.g. "stored-" + "energy ...".

    The hyphen is kept ("stored-energy", "500-hour"); only the line-break space is dropped.
    """
    return len(line) >= 2 and line[-1] == "-" and line[-2].isalnum() and next_line[0].isalnum()


def _validate_identity(
    *, machine_id: str, document_id: str, revision: str, source_filename: str
) -> SourceIdentity:
    values = {
        "machine_id": machine_id,
        "document_id": document_id,
        "revision": revision,
        "source_filename": source_filename,
    }
    cleaned: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise InvalidMetadataError(f"{name} must be a non-empty string, got {value!r}")
        cleaned[name] = value.strip()
    return SourceIdentity(**cleaned)
