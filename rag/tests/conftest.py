"""Shared fixtures: the real challenge manuals, described by the challenge's own CSV."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import pytest

from rag.ingestion.pdf_extractor import extract_pdf
from rag.schemas import ExtractedDocument

REPO_ROOT = Path(__file__).resolve().parents[2]
CHALLENGE_DIR = REPO_ROOT / "challenge-pack"
MANUALS_DIR = CHALLENGE_DIR / "Manuals"
INDEX_CSV = CHALLENGE_DIR / "machine_manual_index.csv"


@dataclass(frozen=True)
class ManualRecord:
    machine_id: str
    model: str
    machine_name: str
    document_id: str
    revision: str
    pdf_filename: str

    @property
    def path(self) -> Path:
        return MANUALS_DIR / self.pdf_filename


def load_manual_records() -> list[ManualRecord]:
    if not INDEX_CSV.is_file():
        raise FileNotFoundError(
            f"{INDEX_CSV} not found. Extract the challenge ZIP into challenge-pack/ before running tests."
        )
    with INDEX_CSV.open(newline="", encoding="utf-8") as fh:
        return [ManualRecord(**row) for row in csv.DictReader(fh)]


MANUAL_RECORDS = load_manual_records()


def record_for(document_id: str) -> ManualRecord:
    return next(r for r in MANUAL_RECORDS if r.document_id == document_id)


def extract_record(record: ManualRecord) -> ExtractedDocument:
    return extract_pdf(
        record.path,
        machine_id=record.machine_id,
        document_id=record.document_id,
        revision=record.revision,
    )


@pytest.fixture(scope="session")
def extracted_manuals() -> dict[str, ExtractedDocument]:
    """All supplied manuals extracted once, keyed by document_id."""
    return {r.document_id: extract_record(r) for r in MANUAL_RECORDS}


@pytest.fixture(scope="session")
def manual_chunks(extracted_manuals) -> dict[str, list]:
    """Default-config chunks of every supplied manual, keyed by document_id."""
    from rag.ingestion.chunker import chunk_document

    return {doc_id: chunk_document(doc) for doc_id, doc in extracted_manuals.items()}
