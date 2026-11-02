"""Retrieval evaluation on the supplied manuals (docs/IMPLEMENTATION_PLAN.md, 13 questions).

Builds a throw-away index in a temporary directory from the real PDFs, runs every evaluation
question through the machine-filtered search, and prints where the expected evidence ranks.
Expected sources are copied from the implementation plan and must not be edited to make the
system look better.

Run:  python -m rag.evaluation [--limit 8]
Needs the embedding model (cached or downloadable). Never touches the project's chroma_db/.
"""

from __future__ import annotations

import argparse
import csv
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rag.config import PROJECT_ROOT, EmbeddingConfig, VectorStoreConfig
from rag.embeddings.embedder import get_embedder
from rag.ingestion.chunker import chunk_document
from rag.ingestion.pdf_extractor import extract_pdf
from rag.schemas import SearchResult
from rag.vectorstore.chroma_store import ChromaVectorStore

CHALLENGE_DIR = PROJECT_ROOT / "challenge-pack"


@dataclass(frozen=True)
class EvalQuestion:
    number: int
    machine_id: str
    question: str
    expected: tuple[tuple[str, int], ...]  # (document_id, pdf_page); empty = should abstain
    note: str = ""


# From docs/IMPLEMENTATION_PLAN.md, "Grounded evaluation questions".
EVAL_QUESTIONS: tuple[EvalQuestion, ...] = (
    EvalQuestion(1, "M-HP200", "What does E17 mean?", (("HP-OPS-01", 2),)),
    EvalQuestion(2, "M-AC90", "What does E17 mean?", (("AC-OM-01", 2),)),
    EvalQuestion(3, "M-HP200", "What is the normal operating pressure range?", (("HP-OPS-01", 1),)),
    EvalQuestion(4, "M-AC90", "What is the normal operating pressure range?", (("AC-OM-01", 1),)),
    EvalQuestion(5, "M-HP200", "How often is the filter checked?", (("HP-MNT-01", 2),)),
    EvalQuestion(
        6, "M-AC90",
        "How often should the AF-10 filter indicator be checked, and when is planned replacement due?",
        (("AC-OM-01", 3),),
    ),
    EvalQuestion(7, "M-HP200", "What should be recorded before escalation?",
                 (("HP-OPS-01", 3), ("HP-MNT-01", 3)), "either source acceptable"),
    EvalQuestion(8, "M-AC90", "What should be recorded before escalation?", (("AC-OM-01", 3),)),
    EvalQuestion(9, "M-HP200", "What triggers E17 and what should be checked first?",
                 (("HP-OPS-01", 2), ("HP-MNT-01", 3)), "multi-document: both required"),
    EvalQuestion(10, "M-HP200", "When is the AF-10 filter replaced?", (), "isolation: AF-10 is AC-90 only"),
    EvalQuestion(11, "M-AC90", "What does E24 mean?", (), "isolation: E24 is HP-200 only"),
    EvalQuestion(12, "M-HP200", "What is the pump replacement price?", (),
                 "unanswerable (HP-OPS-01 p.3 says it is not provided)"),
    EvalQuestion(13, "M-AC90", "What does E31 mean?", (("AC-OM-01", 2),)),
)


def build_index(persist_dir: Path) -> ChromaVectorStore:
    store = ChromaVectorStore(get_embedder(EmbeddingConfig()), VectorStoreConfig(persist_dir, "evaluation_chunks"))
    with (CHALLENGE_DIR / "machine_manual_index.csv").open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            doc = extract_pdf(
                CHALLENGE_DIR / "Manuals" / row["pdf_filename"],
                machine_id=row["machine_id"], document_id=row["document_id"], revision=row["revision"],
            )
            store.index_manual(chunk_document(doc))
    return store


def expected_ranks(results: list[SearchResult], expected: tuple[tuple[str, int], ...]) -> dict[tuple[str, int], int | None]:
    """1-based rank of the first chunk from each expected (document, page), or None if absent."""
    ranks: dict[tuple[str, int], int | None] = {}
    for source in expected:
        ranks[source] = next(
            (i for i, r in enumerate(results, start=1) if (r.document_id, r.pdf_page) == source), None
        )
    return ranks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        store = build_index(Path(tmp))
        for q in EVAL_QUESTIONS:
            results = store.search(q.question, machine_id=q.machine_id, limit=args.limit)
            machines = {r.machine_id for r in results}
            print(f"\nQ{q.number} [{q.machine_id}] {q.question}  {('(' + q.note + ')') if q.note else ''}")
            print(f"   machines in results: {sorted(machines)}")
            for i, r in enumerate(results, start=1):
                print(f"   {i}. d={r.cosine_distance:.3f}  {r.document_id} p{r.pdf_page} ({r.chunk_id})")
            if q.expected:
                print(f"   expected (doc, page) -> rank: {expected_ranks(results, q.expected)}")
            else:
                print("   expected: insufficient evidence (abstain)")


if __name__ == "__main__":
    main()
