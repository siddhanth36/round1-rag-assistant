"""Deterministic, page-aware chunking.

Algorithm (per page — chunks never cross pages, documents, revisions or machines):

1. Split the page text into words, remembering which words end a paragraph.
2. Slide a window of at most ``chunk_size`` words over the page.
3. If the window does not reach the end of the page, move its end back to the last
   paragraph boundary inside the window, provided that keeps the chunk at least half
   full and still advances past the overlap. Otherwise cut at ``chunk_size`` words.
4. The next window starts ``overlap`` words before the previous end, so consecutive
   chunks on the same page share exactly ``overlap`` words.

A page with at most ``chunk_size`` words therefore becomes a single chunk, and a blank
page yields no chunks. Paragraph breaks inside a chunk are preserved as blank lines.
"""

from __future__ import annotations

from collections.abc import Iterable

from rag.config import ChunkingConfig
from rag.schemas import Chunk, ExtractedDocument, ExtractedPage


def make_chunk_id(document_id: str, revision: str, pdf_page: int, chunk_index: int) -> str:
    """Stable chunk identifier derived only from source identity and position."""
    return f"{document_id}:{revision}:p{pdf_page}:c{chunk_index}"


def chunk_document(document: ExtractedDocument, config: ChunkingConfig | None = None) -> list[Chunk]:
    """Chunk every page of one document revision, in page order."""
    config = config or ChunkingConfig()
    chunks: list[Chunk] = []
    for page in document.pages:
        chunks.extend(chunk_page(page, config))
    return chunks


def chunk_page(page: ExtractedPage, config: ChunkingConfig | None = None) -> list[Chunk]:
    """Split one page into chunks that all carry the page's source metadata."""
    config = config or ChunkingConfig()
    words, paragraph_ends = _tokenize(page.page_text)
    return [
        Chunk(
            chunk_id=make_chunk_id(page.document_id, page.revision, page.pdf_page, index),
            chunk_index=index,
            machine_id=page.machine_id,
            document_id=page.document_id,
            revision=page.revision,
            source_filename=page.source_filename,
            pdf_page=page.pdf_page,
            text=_render(words, paragraph_ends, start, end),
        )
        for index, (start, end) in enumerate(_window_bounds(len(words), paragraph_ends, config))
    ]


def _tokenize(text: str) -> tuple[list[str], set[int]]:
    """Return the page's words and the indexes of words that end a paragraph."""
    words: list[str] = []
    paragraph_ends: set[int] = set()
    for paragraph in text.split("\n\n"):
        paragraph_words = paragraph.split()
        if paragraph_words:
            words.extend(paragraph_words)
            paragraph_ends.add(len(words) - 1)
    return words, paragraph_ends


def _window_bounds(
    word_count: int, paragraph_ends: set[int], config: ChunkingConfig
) -> Iterable[tuple[int, int]]:
    """Yield ``(start, end)`` word ranges (end exclusive) covering all words."""
    size, overlap = config.chunk_size, config.overlap
    start = 0
    while start < word_count:
        end = min(start + size, word_count)
        if end < word_count:
            end = _snap_to_paragraph_end(start, end, paragraph_ends, minimum=start + max(overlap, size // 2))
        yield start, end
        if end >= word_count:
            return
        start = end - overlap


def _snap_to_paragraph_end(start: int, end: int, paragraph_ends: set[int], *, minimum: int) -> int:
    """Move ``end`` back to the latest paragraph boundary in ``(minimum, end]``, if any."""
    for boundary in range(end, minimum, -1):  # boundary = exclusive end index
        if boundary - 1 in paragraph_ends:
            return boundary
    return end


def _render(words: list[str], paragraph_ends: set[int], start: int, end: int) -> str:
    parts: list[str] = []
    for i in range(start, end):
        parts.append(words[i])
        if i < end - 1:
            parts.append("\n\n" if i in paragraph_ends else " ")
    return "".join(parts)
