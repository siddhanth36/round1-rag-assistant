"""Contract tests for the interface Django uses (docs/RAG_INTEGRATION_CONTRACT.md).

They call only the public functions/classes listed in the contract, in the documented order,
with a temporary ChromaDB directory, the offline HashingEmbedder and scripted fake LLMs.
If one of these tests has to change, the integration contract has changed: update the
contract document and tell Developer B.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# --- the public interface, imported exactly as the contract documents it ----------------------
from rag.config import AnswerConfig, VectorStoreConfig
from rag.exceptions import (
    InvalidChunkError,
    InvalidMetadataError,
    InvalidPdfError,
    RagError,
    RevisionNotFoundError,
)
from rag.generation.answer_service import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    PROVIDER_UNAVAILABLE_MESSAGE,
    answer_question,
)
from rag.ingestion.chunker import chunk_document
from rag.ingestion.pdf_extractor import extract_pdf
from rag.schemas import AnswerResult, AnswerStatus, Citation, IndexSummary
from rag.vectorstore.chroma_store import ChromaVectorStore

from rag.tests.conftest import REPO_ROOT, record_for
from rag.tests.support import INSUFFICIENT_JSON, HashingEmbedder, ScriptedLLM, answer_json, passages_in_prompt

HP, AC = "M-HP200", "M-AC90"

pytestmark = pytest.mark.filterwarnings("ignore:legacy embedding function config:DeprecationWarning")

PUBLIC_MODULES = (
    "rag.config",
    "rag.exceptions",
    "rag.schemas",
    "rag.ingestion.pdf_extractor",
    "rag.ingestion.chunker",
    "rag.embeddings.embedder",
    "rag.vectorstore.chroma_store",
    "rag.generation.llm_client",
    "rag.generation.answer_service",
)


def index_uploaded_manual(store: ChromaVectorStore, pdf_path: Path, *, machine_id: str, document_id: str,
                          revision: str, original_filename: str, make_current: bool = True) -> IndexSummary:
    """The documented ingestion sequence (contract section "Manual ingestion")."""
    document = extract_pdf(pdf_path, machine_id=machine_id, document_id=document_id, revision=revision,
                           source_filename=original_filename)
    return store.index_manual(chunk_document(document), make_current=make_current)


@pytest.fixture()
def store(tmp_path: Path) -> ChromaVectorStore:
    store = ChromaVectorStore(HashingEmbedder(), VectorStoreConfig(tmp_path / "chroma", "contract_chunks"))
    for document_id in ("HP-OPS-01", "HP-MNT-01", "AC-OM-01"):
        r = record_for(document_id)
        index_uploaded_manual(store, r.path, machine_id=r.machine_id, document_id=r.document_id,
                              revision=r.revision, original_filename=r.pdf_filename)
    return store


def _cite(needle: str):
    """Fake LLM: cite the first passage containing ``needle`` with a statement quoting it."""
    def reply(user_prompt: str) -> str:
        tag = next(t for t, text in passages_in_prompt(user_prompt).items() if needle in text)
        return answer_json((f"{needle}.", [tag]))
    return reply


# --- no Django ---------------------------------------------------------------------------------


def test_public_modules_import_without_django() -> None:
    code = (
        "import importlib, sys\n"
        f"for m in {PUBLIC_MODULES!r}: importlib.import_module(m)\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('django', 'rest_framework'))\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --- answer_question ---------------------------------------------------------------------------


def test_valid_question_returns_answered_result_with_citations(store) -> None:
    result = answer_question("What is the normal hydraulic pressure?", HP, store=store,
                             llm=ScriptedLLM(_cite("120 to 140 bar")))
    assert isinstance(result, AnswerResult)
    assert result.status is AnswerStatus.ANSWERED and result.status == "answered"
    assert result.machine_id == HP and result.citations
    for c in result.citations:
        assert isinstance(c, Citation)
        assert (c.machine_id, c.document_id, c.revision, c.source_filename) == (
            HP, "HP-OPS-01", "1.0", "HP200_Operations_Rev1_0.pdf"
        )
        # "120 to 140 bar" is printed on HP-OPS-01 p.1 and p.2; the citation must be the exact
        # record that was sent to the LLM under its tag.
        evidence = {e.source_tag: e for e in result.retrieval.evidence}[c.source_tag]
        assert (c.chunk_id, c.pdf_page, c.revision) == (evidence.chunk_id, evidence.pdf_page, evidence.revision)
        assert c.pdf_page in (1, 2) and "120 to 140 bar" in c.passage
        assert f"[{c.source_tag}]" in result.answer


def test_result_is_json_serializable_for_api_layer(store) -> None:
    result = answer_question("What is the normal hydraulic pressure?", HP, store=store,
                             llm=ScriptedLLM(_cite("120 to 140 bar")))
    payload = json.loads(json.dumps(dataclasses.asdict(result)))
    assert payload["status"] == "answered"
    assert set(payload["citations"][0]) == {
        "source_tag", "chunk_id", "machine_id", "document_id", "revision", "pdf_page", "source_filename", "passage"
    }


@pytest.mark.parametrize("machine_id", ["", "  ", None])
def test_missing_machine_id_raises_invalid_metadata(store, machine_id) -> None:
    with pytest.raises(InvalidMetadataError):
        answer_question("What does E17 mean?", machine_id, store=store, llm=ScriptedLLM())


@pytest.mark.parametrize("question", ["", "  ", None])
def test_empty_question_raises_invalid_metadata(store, question) -> None:
    with pytest.raises(InvalidMetadataError) as info:
        answer_question(question, HP, store=store, llm=ScriptedLLM())
    assert isinstance(info.value, (RagError, ValueError))  # both catchable, as documented


def test_insufficient_evidence_result(store) -> None:
    result = answer_question("What is the pump replacement price?", HP, store=store,
                             llm=ScriptedLLM(INSUFFICIENT_JSON), config=AnswerConfig(max_distance=None))
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE and result.citations == ()


def test_provider_unavailable_result(store) -> None:
    result = answer_question("What is the normal hydraulic pressure?", HP, store=store, llm=None,
                             config=AnswerConfig(max_distance=None))
    assert result.status is AnswerStatus.PROVIDER_UNAVAILABLE
    assert result.answer == PROVIDER_UNAVAILABLE_MESSAGE and result.citations == () and result.llm_model is None
    assert result.retrieval.evidence  # retrieval still ran and is reported


def test_invalid_citations_never_reach_the_result(store) -> None:
    bad = answer_json(("Normal pressure is 120 to 140 bar.", ["S42"]))
    result = answer_question("What is the normal hydraulic pressure?", HP, store=store, llm=ScriptedLLM(bad, bad))
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.citations == ()
    assert any("S42" in reason for reason in result.rejection_reasons)


def test_answers_for_one_machine_never_cite_another(store) -> None:
    result = answer_question("What does E17 mean?", AC, store=store, llm=ScriptedLLM(_cite("E17")),
                             config=AnswerConfig(max_distance=None, retrieval_limit=50))
    assert result.status is AnswerStatus.ANSWERED
    assert {c.machine_id for c in result.citations} == {AC}
    assert {e.document_id for e in result.retrieval.evidence} == {"AC-OM-01"}


# --- ingestion, revisions, removal -----------------------------------------------------------------


def test_manual_indexing_returns_summary(tmp_path: Path) -> None:
    store = ChromaVectorStore(HashingEmbedder(), VectorStoreConfig(tmp_path / "chroma", "contract_chunks"))
    r = record_for("HP-MNT-01")
    stored_copy = tmp_path / "media" / "HP-MNT-01_rev1.0.pdf"  # Django stores uploads under its own name
    stored_copy.parent.mkdir()
    shutil.copy(r.path, stored_copy)

    summary = index_uploaded_manual(store, stored_copy, machine_id=HP, document_id="HP-MNT-01", revision="1.0",
                                    original_filename=r.pdf_filename)
    assert isinstance(summary, IndexSummary)
    assert (summary.machine_id, summary.document_id, summary.revision) == (HP, "HP-MNT-01", "1.0")
    assert summary.source_filename == r.pdf_filename  # citations show the original name, not the stored one
    assert summary.pages_indexed == (1, 2, 3) and summary.chunks_indexed == store.count() > 0
    assert summary.is_current is True


def test_invalid_upload_raises_invalid_pdf(tmp_path: Path, store) -> None:
    bad = tmp_path / "upload.pdf"
    bad.write_text("not a pdf", encoding="utf-8")
    before = store.count()
    with pytest.raises(InvalidPdfError):
        index_uploaded_manual(store, bad, machine_id=HP, document_id="HP-OPS-01", revision="2.0",
                              original_filename="upload.pdf")
    assert store.count() == before


def test_document_id_reused_for_another_machine_is_rejected(store) -> None:
    r = record_for("HP-OPS-01")
    with pytest.raises(InvalidChunkError):
        index_uploaded_manual(store, r.path, machine_id=AC, document_id="HP-OPS-01", revision="1.0",
                              original_filename=r.pdf_filename)


def test_revision_switching_workflow(tmp_path: Path, store) -> None:
    r = record_for("HP-OPS-01")
    # Same real PDF uploaded under a new revision label, staged (not current) first.
    staged = index_uploaded_manual(store, r.path, machine_id=HP, document_id="HP-OPS-01", revision="1.1",
                                   original_filename=r.pdf_filename, make_current=False)
    assert staged.is_current is False

    def cited_revision() -> str:
        result = answer_question("normal hydraulic pressure", HP, store=store, llm=ScriptedLLM(_cite("120 to 140 bar")),
                                 config=AnswerConfig(max_distance=None, retrieval_limit=50))
        return {c.revision for c in result.citations if c.document_id == "HP-OPS-01"}.pop()

    assert cited_revision() == "1.0"
    assert store.set_current_revision(machine_id=HP, document_id="HP-OPS-01", revision="1.1") > 0
    assert cited_revision() == "1.1"
    store.set_current_revision(machine_id=HP, document_id="HP-OPS-01", revision="1.0")  # roll back
    assert cited_revision() == "1.0"
    with pytest.raises(RevisionNotFoundError):
        store.set_current_revision(machine_id=HP, document_id="HP-OPS-01", revision="9.9")


def test_removing_a_revision(store) -> None:
    total = store.count()
    removed = store.remove_manual(machine_id=HP, document_id="HP-MNT-01", revision="1.0")
    assert 0 < removed < total and store.count() == total - removed
    assert store.remove_manual(machine_id=AC, document_id="HP-OPS-01", revision=None) == 0  # wrong machine: no-op
