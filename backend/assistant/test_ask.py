"""POST /api/assistant/ask/. The rag adapter is stubbed ONLY here; isolation tests run real rag retrieval."""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from backend.accounts.models import User
from backend.assistant import rag_adapter
from backend.assistant.models import Answer, Citation, Question
from backend.machines.models import Machine, Manual

MANUALS_DIR = settings.BASE_DIR / "challenge-pack" / "Manuals"
CORPUS = [("M-HP200", "HP-OPS-01", "HP200_Operations_Rev1_0.pdf"), ("M-HP200", "HP-MNT-01", "HP200_Maintenance_Rev1_0.pdf"),
          ("M-AC90", "AC-OM-01", "AC90_Operations_Maintenance_Rev1_0.pdf")]


def cite(tag, doc, machine="M-HP200", page=2):
    return SimpleNamespace(source_tag=tag, chunk_id=f"{doc}:1.0:p{page}:c0", machine_id=machine, document_id=doc,
                           revision="1.0", pdf_page=page, source_filename=f"{doc}.pdf", passage=f"passage for {tag}")


def rag_result(status, answer="", citations=()):
    """Shape-compatible stand-in for rag.schemas.AnswerResult."""
    evidence = [SimpleNamespace(source_tag=c.source_tag, chunk_id=c.chunk_id, document_id=c.document_id, revision="1.0",
                                pdf_page=c.pdf_page, cosine_distance=0.4) for c in citations]
    return SimpleNamespace(status=status, answer=answer, citations=tuple(citations), llm_model="stub-llm", rejection_reasons=(),
                           error=None, retrieval=SimpleNamespace(retrieved_count=len(evidence), limit=8, max_distance=0.8,
                                                                 evidence=tuple(evidence)))


class AskTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.engineer = User.objects.create_user(username="engineer", password="x", role="ENGINEER")
        self.other = User.objects.create_user(username="engineer2", password="x", role="ENGINEER")
        admin = User.objects.create_user(username="admin", password="x", role="ADMIN")
        self.machines = {"M-HP200": Machine.objects.create(machine_id="M-HP200", model="HP-200", machine_name="Hydraulic forming press"),
                         "M-AC90": Machine.objects.create(machine_id="M-AC90", model="AC-90", machine_name="Air compressor")}
        for machine_id, doc, filename in CORPUS:
            Manual.objects.create(machine=self.machines[machine_id], document_id=doc, revision="1.0", original_filename=filename,
                                  file=f"manuals/{doc}.pdf", page_count=3, indexing_status=Manual.Status.INDEXED, uploaded_by=admin)
        self.client.force_authenticate(self.engineer)

    def ask(self, question="What does E17 mean?", machine_id="M-HP200", **extra):
        return self.client.post("/api/assistant/ask/", {"machine_id": machine_id, "question": question, **extra}, format="json")


class AskStubbedTests(AskTestCase):
    def stub(self, value):
        target = mock.patch.object(rag_adapter, "ask", side_effect=value) if isinstance(value, Exception) \
            else mock.patch.object(rag_adapter, "ask", return_value=value)
        return self.enterContext(target)

    def test_answered_is_persisted_with_citations_trace_latency_and_session_user(self):
        spy = self.stub(rag_result("answered", "E17 is low pressure [S1]. Check RF-20 [S2].",
                                   [cite("S1", "HP-OPS-01"), cite("S2", "HP-MNT-01", page=3)]))
        response = self.ask(user_id=self.other.id, user=self.other.username)  # body user fields must be ignored
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertEqual(body["status"], "answered")
        self.assertEqual([(c["source_tag"], c["document_id"], c["pdf_page"]) for c in body["citations"]],
                         [("S1", "HP-OPS-01", 2), ("S2", "HP-MNT-01", 3)])
        self.assertNotIn("retrieval_trace", body)  # engineers never see diagnostics
        question = Question.objects.get()
        self.assertEqual(question.user, self.engineer)
        self.assertEqual(question.answer.retrieval_trace["llm_model"], "stub-llm")
        self.assertIsInstance(question.answer.latency_ms, int)
        self.assertEqual(spy.call_args.args, ("What does E17 mean?", "M-HP200"))  # machine_id from the DB row

    def test_citation_from_another_machine_is_dropped_and_answer_downgraded(self):
        self.stub(rag_result("answered", "Something [S1].", [cite("S1", "AC-OM-01", machine="M-AC90")]))
        body = self.ask().json()
        self.assertEqual((body["status"], body["citations"]), ("insufficient_evidence", []))
        self.assertEqual(Citation.objects.count(), 0)
        self.assertEqual(len(Answer.objects.get().retrieval_trace["dropped_citations"]), 1)

    def test_unresolvable_tag_in_text_downgrades_even_if_other_citations_resolve(self):
        self.stub(rag_result("answered", "Fact one [S1]. Fact two [S2].",
                             [cite("S1", "HP-OPS-01"), cite("S2", "HP-OPS-01", page=9)]))  # page 9 > page_count
        self.assertEqual(self.ask().json()["status"], "insufficient_evidence")

    def test_unreferenced_bad_citation_is_dropped_but_answer_kept(self):
        self.stub(rag_result("answered", "Fact one [S1].", [cite("S1", "HP-OPS-01"), cite("S2", "UNKNOWN-DOC")]))
        body = self.ask().json()
        self.assertEqual((body["status"], [c["source_tag"] for c in body["citations"]]), ("answered", ["S1"]))

    def test_status_mapping_and_failures_are_logged_as_error_never_answers(self):
        cases = [rag_result("insufficient_evidence", "The available manuals ... information"),
                 rag_result("provider_unavailable", "no provider"), rag_result("generation_error", "bad output"),
                 RuntimeError("groq timeout with secret-ish detail")]
        expected = ["insufficient_evidence", "error", "error", "error"]
        for value, status in zip(cases, expected):
            with self.subTest(status=status, value=type(value).__name__):
                with (mock.patch.object(rag_adapter, "ask", side_effect=value) if isinstance(value, Exception)
                      else mock.patch.object(rag_adapter, "ask", return_value=value)):
                    body = self.ask().json()
                self.assertEqual((body["status"], body["citations"]), (status, []))
                if status == "error":
                    self.assertNotIn("secret", body["answer"])
                    self.assertIn("could not complete", body["answer"])
        self.assertEqual(Question.objects.count(), 4)
        self.assertEqual(Answer.objects.count(), 4)  # every outcome is logged

    def test_permissions_and_validation(self):
        self.stub(rag_result("insufficient_evidence", "x"))
        self.client.force_authenticate(User.objects.create_user(username="sup", password="x", role="SUPERVISOR"))
        self.assertEqual(self.ask().status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.ask().status_code, 401)
        self.client.force_authenticate(self.engineer)
        self.assertEqual(self.ask(question="   ").status_code, 400)
        self.assertEqual(self.ask(machine_id="M-NOPE").status_code, 400)
        self.assertEqual(self.ask(question="x" * 1001).status_code, 400)
        self.assertEqual(Question.objects.count(), 0)


@pytest.mark.skipif(not (MANUALS_DIR / CORPUS[0][2]).is_file(), reason="challenge-pack/Manuals not present")
class MachineIsolationTests(AskTestCase):
    """Brief requirement: retrieval excludes passages from another machine. Real rag extraction, chunking,
    Chroma filtering and citation validation; only the embedder (hashing) and the LLM (scripted) are fakes."""

    def setUp(self):
        super().setUp()
        from rag.config import VectorStoreConfig
        from rag.tests.support import HashingEmbedder, ScriptedLLM, answer_json, passages_in_prompt
        from rag.vectorstore.chroma_store import ChromaVectorStore

        # ignore_cleanup_errors: on Windows chromadb keeps its index files open until process exit
        tmp = self.enterContext(tempfile.TemporaryDirectory(ignore_cleanup_errors=True))
        store = ChromaVectorStore(HashingEmbedder(), VectorStoreConfig(Path(tmp), "iso"))
        self.enterContext(mock.patch.object(rag_adapter, "get_store", return_value=store))
        self.enterContext(mock.patch.dict(os.environ, {"RAG_MAX_DISTANCE": "off"}))  # send every retrieved passage to the LLM
        for machine_id, doc, filename in CORPUS:
            rag_adapter.index_manual(MANUALS_DIR / filename, machine_id=machine_id, document_id=doc, revision="1.0",
                                     source_filename=filename)

        def cite_everything(user_prompt):  # the LLM cites every passage it was shown
            return answer_json(*[(text, [tag]) for tag, text in passages_in_prompt(user_prompt).items()])
        self.enterContext(mock.patch.object(rag_adapter, "get_llm", return_value=ScriptedLLM(*[cite_everything] * 4)))

    def assert_isolated(self, machine_id, allowed):
        body = self.ask("What does E17 mean?", machine_id).json()
        trace = Answer.objects.get(question_id=body["id"]).retrieval_trace
        self.assertTrue(trace["evidence"], "retrieval returned nothing")
        self.assertEqual({e["document_id"] for e in trace["evidence"]} - allowed, set())  # nothing foreign reached the LLM
        self.assertEqual({c["document_id"] for c in body["citations"]} - allowed, set())
        return body

    def test_ac90_question_never_cites_hp200_manuals(self):
        body = self.assert_isolated("M-AC90", {"AC-OM-01"})
        self.assertEqual(body["status"], "answered")
        self.assertNotIn("HP-OPS-01", str(body))
        self.assertNotIn("HP-MNT-01", str(body))

    def test_hp200_question_never_cites_ac90_manual(self):
        body = self.assert_isolated("M-HP200", {"HP-OPS-01", "HP-MNT-01"})
        self.assertEqual(body["status"], "answered")
        self.assertNotIn("AC-OM-01", str(body))


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="GROQ_API_KEY not set")
class LiveGroqTests(AskTestCase):
    """Real engine end to end: real embedder, the indexed chroma_db/, and Groq."""

    def test_e17_on_hp200_is_answered_from_hp200_manuals(self):
        body = self.ask("What does E17 mean?", "M-HP200").json()
        self.assertEqual(body["status"], "answered", body)
        self.assertTrue(body["citations"])
        self.assertTrue({c["document_id"] for c in body["citations"]} <= {"HP-OPS-01", "HP-MNT-01"})
