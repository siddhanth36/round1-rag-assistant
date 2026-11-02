"""End-to-end answer pipeline on the real manuals: real PDFs -> MiniLM -> ChromaDB -> answer_question.

The LLM is a deterministic extractive fake (rag/tests/support.extractive_reply) that can only
quote passages present in the prompt it receives. These tests therefore prove that the
correct machine's evidence reaches the LLM and that citations resolve to the right
machine/document/page. They do NOT test a real LLM's reasoning (no provider is configured).
Skipped if the embedding model cannot be loaded.
"""

from __future__ import annotations

import pytest

from rag.config import EmbeddingConfig, VectorStoreConfig
from rag.embeddings.embedder import get_embedder
from rag.exceptions import EmbeddingModelError
from rag.generation.answer_service import answer_question
from rag.schemas import AnswerStatus
from rag.tests.support import ScriptedLLM, extractive_reply, passages_in_prompt
from rag.vectorstore.chroma_store import ChromaVectorStore

HP, AC = "M-HP200", "M-AC90"

pytestmark = pytest.mark.filterwarnings("ignore:legacy embedding function config:DeprecationWarning")


@pytest.fixture(scope="module")
def real_store(tmp_path_factory, manual_chunks) -> ChromaVectorStore:
    try:
        embedder = get_embedder(EmbeddingConfig())
    except EmbeddingModelError as exc:
        pytest.skip(f"Embedding model unavailable: {exc}")
    store = ChromaVectorStore(embedder, VectorStoreConfig(tmp_path_factory.mktemp("chroma"), "answer_integration"))
    for chunks in manual_chunks.values():
        store.index_manual(chunks)
    return store


def _ask(store, question: str, machine_id: str, *needles: str):
    reply = extractive_reply(*needles)
    llm = ScriptedLLM(reply, reply)  # same behaviour if a correction round happens
    return answer_question(question, machine_id, store=store, llm=llm), llm


# Meanings as printed in each machine's own alarm table (HP-OPS-01 p.2 / AC-OM-01 p.2).
CODE_MEANINGS = {
    (HP, "E17"): ("E17 Low hydraulic pressure", "HP-OPS-01"),
    (AC, "E17"): ("E17 High discharge temperature", "AC-OM-01"),
    (HP, "E31"): ("E31 Guard-open indication", "HP-OPS-01"),
    (AC, "E31"): ("E31 Condensate-drain fault", "AC-OM-01"),
    (HP, "E42"): ("E42 Return-filter restriction", "HP-OPS-01"),
    (AC, "E42"): ("E42 Inlet-filter restriction", "AC-OM-01"),
}


@pytest.mark.parametrize("machine_id, code", list(CODE_MEANINGS))
def test_error_code_meaning_comes_from_selected_machine(real_store, machine_id, code) -> None:
    table_row, document_id = CODE_MEANINGS[(machine_id, code)]
    result, llm = _ask(real_store, f"What does {code} mean?", machine_id, table_row)

    assert result.status is AnswerStatus.ANSWERED, result.rejection_reasons
    assert table_row in result.answer
    assert {(c.machine_id, c.document_id, c.pdf_page) for c in result.citations} == {(machine_id, document_id, 2)}
    other = AC if machine_id == HP else HP
    other_row = CODE_MEANINGS[(other, code)][0]
    prompt = llm.prompts[0][1]
    assert other_row not in prompt  # the other machine's meaning never reaches the LLM
    assert f'machine="{other}"' not in prompt


def test_hp200_multi_manual_answer_cites_both_manuals(real_store) -> None:
    result, _ = _ask(
        real_store, "What triggers E17 and what should be checked first?", HP,
        "E17 is generated when pressure stays below 110 bar", "Collect these observations in order",
    )
    assert result.status is AnswerStatus.ANSWERED, result.rejection_reasons
    assert [(c.document_id, c.pdf_page) for c in result.citations] == [("HP-OPS-01", 2), ("HP-MNT-01", 3)]
    assert "110 bar" in result.answer and "reservoir level" in result.answer


@pytest.mark.parametrize(
    "question, machine_id, foreign_term",
    [
        ("When is the AF-10 filter replaced?", HP, "AF-10"),  # AF-10 exists only in AC-OM-01
        ("What does E24 mean?", AC, "E24"),                    # E24 exists only in HP-200 manuals
        ("What does E08 mean?", HP, "E08"),                    # E08 exists only in AC-OM-01
    ],
)
def test_other_machine_only_evidence_yields_insufficient(real_store, question, machine_id, foreign_term) -> None:
    result, llm = _ask(real_store, question, machine_id, foreign_term)
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.citations == ()
    passages = passages_in_prompt(llm.prompts[0][1])
    assert passages and all(foreign_term not in text for text in passages.values())


def test_no_provider_still_reports_retrieval(real_store) -> None:
    result = answer_question("What is the normal operating pressure range?", AC, store=real_store, llm=None)
    assert result.status is AnswerStatus.PROVIDER_UNAVAILABLE
    assert result.retrieval.evidence and all(e.document_id == "AC-OM-01" for e in result.retrieval.evidence)


def test_off_topic_question_abstains_on_relevance_threshold(real_store) -> None:
    llm = ScriptedLLM()
    result = answer_question("What is the capital of France?", HP, store=real_store, llm=llm)
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert llm.prompts == [] and result.retrieval.retrieved_count > 0 and result.retrieval.evidence == ()
