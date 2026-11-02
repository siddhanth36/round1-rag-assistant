"""Tests for rag.generation.answer_service with deterministic fake LLMs (no network)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from rag.config import AnswerConfig, VectorStoreConfig
from rag.exceptions import InvalidMetadataError, LLMProviderError
from rag.generation.answer_service import (
    GENERATION_ERROR_MESSAGE,
    INSUFFICIENT_EVIDENCE_MESSAGE,
    PROVIDER_UNAVAILABLE_MESSAGE,
    answer_question,
)
from rag.generation.prompts import SYSTEM_PROMPT
from rag.ingestion.chunker import make_chunk_id
from rag.schemas import AnswerStatus
from rag.tests.support import (
    INSUFFICIENT_JSON,
    HashingEmbedder,
    ScriptedLLM,
    StubStore,
    answer_json,
    passages_in_prompt,
    search_result,
)
from rag.vectorstore.chroma_store import ChromaVectorStore

HP, AC = "M-HP200", "M-AC90"

pytestmark = pytest.mark.filterwarnings("ignore:legacy embedding function config:DeprecationWarning")


def _chunk(manual_chunks, chunk_id: str):
    return next(c for chunks in manual_chunks.values() for c in chunks if c.chunk_id == chunk_id)


@pytest.fixture()
def hp_store(manual_chunks) -> StubStore:
    """Two HP-200 passages: HP-OPS-01 p2 (alarm table) and HP-MNT-01 p3 (troubleshooting)."""
    return StubStore([
        search_result(_chunk(manual_chunks, "HP-OPS-01:1.0:p2:c0"), 0.40),
        search_result(_chunk(manual_chunks, "HP-MNT-01:1.0:p3:c0"), 0.45),
    ])


GOOD_E17 = answer_json(("E17 Low hydraulic pressure.", ["S1"]))


# --- input validation ------------------------------------------------------------------------


@pytest.mark.parametrize("machine_id", ["", "   ", None])
def test_missing_machine_id_raises(hp_store, machine_id) -> None:
    with pytest.raises(InvalidMetadataError, match="machine_id"):
        answer_question("What does E17 mean?", machine_id, store=hp_store, llm=ScriptedLLM())
    assert hp_store.calls == []


@pytest.mark.parametrize("question", ["", "   ", None])
def test_empty_question_raises(hp_store, question) -> None:
    with pytest.raises(InvalidMetadataError, match="question"):
        answer_question(question, HP, store=hp_store, llm=ScriptedLLM())
    assert hp_store.calls == []


def test_oversized_question_raises(hp_store) -> None:
    with pytest.raises(InvalidMetadataError, match="exceeds"):
        answer_question("x" * 1001, HP, store=hp_store, llm=ScriptedLLM())


# --- retrieval and evidence passed to the LLM --------------------------------------------------


def test_retrieval_uses_selected_machine_and_configured_limit(hp_store) -> None:
    answer_question(" What does E17 mean? ", f" {HP} ", store=hp_store, llm=ScriptedLLM(GOOD_E17),
                    config=AnswerConfig(retrieval_limit=5))
    assert hp_store.calls == [("What does E17 mean?", HP, 5)]


def test_only_retrieved_passages_are_sent_with_tags(hp_store) -> None:
    llm = ScriptedLLM(GOOD_E17)
    answer_question("What does E17 mean?", HP, store=hp_store, llm=llm)
    (system_prompt, user_prompt), = llm.prompts
    assert system_prompt == SYSTEM_PROMPT
    assert passages_in_prompt(user_prompt) == {"S1": hp_store.results[0].text, "S2": hp_store.results[1].text}
    assert "Selected machine: M-HP200" in user_prompt
    assert "M-AC90" not in user_prompt


def test_no_retrieved_evidence_abstains_without_calling_llm() -> None:
    llm = ScriptedLLM()
    result = answer_question("What does E17 mean?", HP, store=StubStore([]), llm=llm)
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.citations == () and llm.prompts == []
    assert result.retrieval.retrieved_count == 0


def test_passages_beyond_distance_threshold_are_not_sent(manual_chunks) -> None:
    store = StubStore([
        search_result(_chunk(manual_chunks, "HP-OPS-01:1.0:p1:c0"), 0.50),
        search_result(_chunk(manual_chunks, "HP-MNT-01:1.0:p2:c0"), 0.95),
    ])
    llm = ScriptedLLM(answer_json(("Normal hydraulic pressure is 120 to 140 bar.", ["S1"])))
    result = answer_question("pressure?", HP, store=store, llm=llm, config=AnswerConfig(max_distance=0.8))
    assert list(passages_in_prompt(llm.prompts[0][1])) == ["S1"]
    assert result.retrieval.retrieved_count == 2
    assert [e.chunk_id for e in result.retrieval.evidence] == ["HP-OPS-01:1.0:p1:c0"]


def test_all_passages_beyond_threshold_abstain(manual_chunks) -> None:
    store = StubStore([search_result(_chunk(manual_chunks, "HP-OPS-01:1.0:p1:c0"), 0.9)])
    llm = ScriptedLLM()
    result = answer_question("capital of France?", HP, store=store, llm=llm, config=AnswerConfig(max_distance=0.8))
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE and llm.prompts == []


def test_threshold_can_be_disabled(manual_chunks) -> None:
    store = StubStore([search_result(_chunk(manual_chunks, "HP-OPS-01:1.0:p1:c0"), 1.5)])
    llm = ScriptedLLM(INSUFFICIENT_JSON)
    answer_question("q", HP, store=store, llm=llm, config=AnswerConfig(max_distance=None))
    assert len(llm.prompts) == 1


# --- outcomes --------------------------------------------------------------------------------


def test_valid_answer_is_returned_with_resolved_citations(hp_store) -> None:
    result = answer_question("What does E17 mean?", HP, store=hp_store, llm=ScriptedLLM(GOOD_E17, model_name="m1"))
    assert result.status is AnswerStatus.ANSWERED
    assert result.answer == "E17 Low hydraulic pressure. [S1]"
    (c,) = result.citations
    assert (c.source_tag, c.machine_id, c.document_id, c.revision, c.pdf_page) == ("S1", HP, "HP-OPS-01", "1.0", 2)
    assert c.passage == hp_store.results[0].text
    assert (result.machine_id, result.question, result.llm_model) == (HP, "What does E17 mean?", "m1")
    assert [e.source_tag for e in result.retrieval.evidence] == ["S1", "S2"]


def test_llm_insufficient_evidence_is_respected(hp_store) -> None:
    result = answer_question("What is the pump price?", HP, store=hp_store, llm=ScriptedLLM(INSUFFICIENT_JSON))
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE and result.citations == ()


def test_multiple_manual_answer_cites_both_documents(hp_store) -> None:
    llm = ScriptedLLM(answer_json(
        ("E17 Low hydraulic pressure.", ["S1"]),
        ("Collect observations in order, starting with reservoir level relative to MIN and MAX.", ["S2"]),
    ))
    result = answer_question("What does E17 mean and what is checked first?", HP, store=hp_store, llm=llm)
    assert result.status is AnswerStatus.ANSWERED
    assert [(c.document_id, c.pdf_page) for c in result.citations] == [("HP-OPS-01", 2), ("HP-MNT-01", 3)]


def test_invalid_tag_is_corrected_on_retry(hp_store) -> None:
    llm = ScriptedLLM(answer_json(("E17 Low hydraulic pressure.", ["S9"])), GOOD_E17)
    result = answer_question("What does E17 mean?", HP, store=hp_store, llm=llm)
    assert result.status is AnswerStatus.ANSWERED
    retry_prompt = llm.prompts[1][1]
    assert "unknown source tag(s) S9" in retry_prompt and "Your previous reply" in retry_prompt


@pytest.mark.parametrize(
    "bad_draft, reason",
    [
        (answer_json(("E17 Low hydraulic pressure.", ["S9"])), "unknown source tag"),
        (answer_json(("E17 Low hydraulic pressure.", [])), "no source tag"),
        (answer_json(("E17 Low hydraulic pressure, see page 9.", ["S1"])), "mentions page 9"),
        (answer_json(("HP-ABC-77 says E17 is Low hydraulic pressure.", ["S1"])), "mentions document HP-ABC-77"),
        (answer_json(("E17 means pressure below 95 bar.", ["S1"])), "value 95"),
    ],
)
def test_unsupported_answer_rejected_after_corrections(hp_store, bad_draft, reason) -> None:
    llm = ScriptedLLM(bad_draft, bad_draft)
    result = answer_question("What does E17 mean?", HP, store=hp_store, llm=llm)
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE and result.citations == ()
    assert any(reason in r for r in result.rejection_reasons)
    assert len(llm.prompts) == 2  # original + one correction


def test_zero_corrections_rejects_immediately(hp_store) -> None:
    llm = ScriptedLLM(answer_json(("E17 Low hydraulic pressure.", ["S9"])))
    result = answer_question("q", HP, store=hp_store, llm=llm, config=AnswerConfig(max_corrections=0))
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE and len(llm.prompts) == 1


def test_tags_from_a_previous_request_are_not_accepted(hp_store, manual_chunks) -> None:
    """A reply valid for an 8-source request cites S5; the next request has only 2 sources."""
    big_store = StubStore([search_result(c) for c in manual_chunks["HP-OPS-01"]])
    first = ScriptedLLM(answer_json(("E17 Low hydraulic pressure.", ["S3"])))
    assert answer_question("q", HP, store=big_store, llm=first).status is AnswerStatus.ANSWERED

    replay = answer_json(("Oil temperature high stop 65 degrees C or higher.", ["S5"]))
    result = answer_question("q", HP, store=hp_store, llm=ScriptedLLM(replay, replay))
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert any("unknown source tag(s) S5" in r for r in result.rejection_reasons)


def test_same_tag_resolves_to_current_request_evidence(hp_store, manual_chunks) -> None:
    other = StubStore([search_result(_chunk(manual_chunks, "HP-MNT-01:1.0:p3:c0"))])
    llm = ScriptedLLM(answer_json(("Collect these observations in order.", ["S1"])))
    result = answer_question("q", HP, store=other, llm=llm)
    assert result.citations[0].chunk_id == "HP-MNT-01:1.0:p3:c0"


# --- provider problems -------------------------------------------------------------------------


def test_no_provider_configured(hp_store) -> None:
    result = answer_question("What does E17 mean?", HP, store=hp_store, llm=None)
    assert result.status is AnswerStatus.PROVIDER_UNAVAILABLE
    assert result.answer == PROVIDER_UNAVAILABLE_MESSAGE and result.citations == ()
    assert result.retrieval.retrieved_count == 2 and result.llm_model is None


def test_provider_error_becomes_generation_error(hp_store) -> None:
    result = answer_question("q", HP, store=hp_store, llm=ScriptedLLM(LLMProviderError("timeout after 30s")))
    assert result.status is AnswerStatus.GENERATION_ERROR
    assert result.answer == GENERATION_ERROR_MESSAGE
    assert result.error == "LLMProviderError: timeout after 30s"


def test_unexpected_client_exception_does_not_crash_or_leak(hp_store) -> None:
    secret = "sk-test-not-a-real-key"
    result = answer_question("q", HP, store=hp_store, llm=ScriptedLLM(RuntimeError(f"boom {secret}")))
    assert result.status is AnswerStatus.GENERATION_ERROR
    assert result.error == "RuntimeError"
    assert secret not in repr(result)


@pytest.mark.parametrize("bad", ["not json at all", "{}", json.dumps({"status": "answered", "statements": "x"})])
def test_malformed_response_twice_is_generation_error(hp_store, bad) -> None:
    llm = ScriptedLLM(bad, bad)
    result = answer_question("q", HP, store=hp_store, llm=llm)
    assert result.status is AnswerStatus.GENERATION_ERROR
    assert result.error == "malformed LLM response"
    assert any("malformed response" in r for r in result.rejection_reasons)


def test_malformed_then_valid_recovers(hp_store) -> None:
    result = answer_question("q", HP, store=hp_store, llm=ScriptedLLM("oops", GOOD_E17))
    assert result.status is AnswerStatus.ANSWERED


# --- revision filtering through the real vector store (offline embedder) ------------------------


def test_only_current_revision_reaches_llm_and_citations(tmp_path: Path, manual_chunks) -> None:
    store = ChromaVectorStore(HashingEmbedder(), VectorStoreConfig(tmp_path / "chroma", "answer_chunks"))
    for chunks in manual_chunks.values():
        store.index_manual(chunks)
    relabeled = [
        replace(c, revision="1.1", chunk_id=make_chunk_id(c.document_id, "1.1", c.pdf_page, c.chunk_index))
        for c in manual_chunks["HP-OPS-01"]
    ]
    store.index_manual(relabeled)  # 1.1 becomes current; 1.0 is kept but not current

    def cite_first_ops_passage(user_prompt: str) -> str:
        tag = next(t for t, text in passages_in_prompt(user_prompt).items() if "HP 200 Operations Manual" in text)
        return answer_json(("HP 200 Operations Manual.", [tag]))

    llm = ScriptedLLM(cite_first_ops_passage)
    result = answer_question("HP 200 Operations Manual normal hydraulic pressure", HP, store=store, llm=llm,
                             config=AnswerConfig(retrieval_limit=20, max_distance=None))
    ops_evidence = [e for e in result.retrieval.evidence if e.document_id == "HP-OPS-01"]
    assert ops_evidence and {e.revision for e in ops_evidence} == {"1.1"}
    assert 'document="HP-OPS-01" revision="1.0"' not in llm.prompts[0][1]
    assert [(c.document_id, c.revision) for c in result.citations] == [("HP-OPS-01", "1.1")]
    assert {e.machine_id for e in store.search("x", machine_id=HP, limit=20)} == {HP}  # sanity
