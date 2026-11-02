from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from backend.accounts.models import User
from backend.assistant.models import Answer, Citation, Question
from backend.machines.models import Machine, Manual

STATUS = Answer.Status


class AssistantTestCase(TestCase):
    """Question/Answer rows are created directly: these endpoints never call rag/."""

    def setUp(self):
        self.client = APIClient()
        self.users = {name: User.objects.create_user(username=name, password="x", role=role) for name, role in
                      [("admin", "ADMIN"), ("engineer", "ENGINEER"), ("engineer2", "ENGINEER"), ("supervisor", "SUPERVISOR")]}
        self.hp = Machine.objects.create(machine_id="M-HP200", model="HP-200", machine_name="Hydraulic forming press")
        self.ac = Machine.objects.create(machine_id="M-AC90", model="AC-90", machine_name="Air compressor")
        self.manual = Manual.objects.create(machine=self.hp, document_id="HP-OPS-01", revision="1.0",
                                            original_filename="HP200_Operations_Rev1_0.pdf", file="manuals/x.pdf",
                                            page_count=3, uploaded_by=self.users["admin"])

    def ask(self, user, machine, text, status=None, days_ago=0):
        q = Question.objects.create(user=self.users[user], machine=machine, text=text)
        if days_ago:
            Question.objects.filter(pk=q.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        if status:
            Answer.objects.create(question=q, text=f"answer to {text}", status=status,
                                  retrieval_trace={"retrieved_count": 2}, latency_ms=1200)
        return q

    def get(self, user, url):
        self.client.force_authenticate(self.users[user])
        return self.client.get(url)


class HistoryTests(AssistantTestCase):
    def test_engineer_sees_only_own_questions_newest_first_with_citations(self):
        first = self.ask("engineer", self.hp, "What does E17 mean?", STATUS.ANSWERED, days_ago=1)
        Citation.objects.create(answer=first.answer, manual=self.manual, document_id="HP-OPS-01", revision="1.0",
                                pdf_page=2, passage="verbatim passage", source_tag="S1")
        self.ask("engineer", self.ac, "Pump price?", STATUS.INSUFFICIENT_EVIDENCE)
        self.ask("engineer2", self.hp, "engineer2's question", STATUS.ANSWERED)

        body = self.get("engineer", "/api/history/").json()
        self.assertEqual(body["count"], 2)
        self.assertEqual([r["question"] for r in body["results"]], ["Pump price?", "What does E17 mean?"])
        answered = body["results"][1]
        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["citations"], [{"source_tag": "S1", "manual_id": self.manual.id,
                                                  "document_id": "HP-OPS-01", "revision": "1.0", "pdf_page": 2,
                                                  "passage": "verbatim passage"}])
        self.assertNotIn("retrieval_trace", answered)  # diagnostics are staff-only

    def test_engineer2_cannot_see_engineers_history(self):
        self.ask("engineer", self.hp, "engineer's private question", STATUS.ANSWERED)
        body = self.get("engineer2", "/api/history/").json()
        self.assertEqual(body["count"], 0)

    def test_history_filters_by_machine_and_status_within_own_questions(self):
        self.ask("engineer", self.hp, "hp answered", STATUS.ANSWERED)
        self.ask("engineer", self.hp, "hp insufficient", STATUS.INSUFFICIENT_EVIDENCE)
        self.ask("engineer", self.ac, "ac error", STATUS.ERROR)
        self.ask("engineer2", self.hp, "other engineer unanswered", STATUS.ERROR)

        def texts(query):
            return sorted(r["question"] for r in self.get("engineer", f"/api/history/{query}").json()["results"])

        self.assertEqual(texts("?machine=M-HP200"), ["hp answered", "hp insufficient"])
        self.assertEqual(texts("?status=unanswered"), ["ac error", "hp insufficient"])
        self.assertEqual(texts("?machine=M-HP200&status=answered"), ["hp answered"])
        self.assertEqual(texts("?user=engineer2"), ["ac error", "hp answered", "hp insufficient"])  # ?user is ignored here
        self.assertEqual(self.get("engineer", "/api/history/?status=nope").status_code, 400)

    def test_history_is_engineer_only(self):
        for user in ("admin", "supervisor"):
            self.assertEqual(self.get(user, "/api/history/").status_code, 403)


class SupervisorTests(AssistantTestCase):
    def test_engineer_is_forbidden_from_supervisor_endpoints(self):
        for url in ("/api/supervisor/questions/", "/api/supervisor/analytics/"):
            self.assertEqual(self.get("engineer", url).status_code, 403)

    def test_supervisor_endpoints_are_read_only(self):
        self.client.force_authenticate(self.users["supervisor"])
        self.assertEqual(self.client.post("/api/supervisor/questions/", {}).status_code, 405)
        self.assertEqual(self.client.delete("/api/supervisor/questions/").status_code, 405)

    def test_question_log_filters(self):
        self.ask("engineer", self.hp, "answered hp", STATUS.ANSWERED)
        self.ask("engineer", self.hp, "insufficient hp", STATUS.INSUFFICIENT_EVIDENCE)
        self.ask("engineer2", self.ac, "error ac", STATUS.ERROR)
        self.ask("engineer2", self.ac, "no answer row ac")
        self.ask("engineer", self.hp, "old answered hp", STATUS.ANSWERED, days_ago=10)

        def texts(query):
            response = self.get("supervisor", f"/api/supervisor/questions/{query}")
            self.assertEqual(response.status_code, 200, response.content)
            return sorted(r["question"] for r in response.json()["results"])

        self.assertEqual(texts("?status=unanswered"), ["error ac", "insufficient hp", "no answer row ac"])
        self.assertEqual(texts("?status=error"), ["error ac"])
        self.assertEqual(texts("?machine=M-AC90"), ["error ac", "no answer row ac"])
        self.assertEqual(texts("?user=engineer&status=answered"), ["answered hp", "old answered hp"])
        since = (timezone.localdate() - timedelta(days=2)).isoformat()
        self.assertEqual(len(texts(f"?from={since}")), 4)
        self.assertEqual(self.get("supervisor", "/api/supervisor/questions/?status=bogus").status_code, 400)
        self.assertEqual(self.get("supervisor", "/api/supervisor/questions/?from=yesterday").status_code, 400)
        staff_row = self.get("admin", "/api/supervisor/questions/?status=error").json()["results"][0]
        self.assertEqual((staff_row["asked_by"], staff_row["retrieval_trace"]), ("engineer2", {"retrieved_count": 2}))

    def test_analytics_counts(self):
        for status in (STATUS.ANSWERED, STATUS.ANSWERED, STATUS.INSUFFICIENT_EVIDENCE):
            self.ask("engineer", self.hp, f"hp {status}", status)
        self.ask("engineer2", self.ac, "ac error", STATUS.ERROR, days_ago=2)
        self.ask("engineer2", self.ac, "ac no answer", days_ago=10)  # outside the 7-day window

        body = self.get("supervisor", "/api/supervisor/analytics/").json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["by_status"], {"answered": 2, "insufficient_evidence": 1, "error": 1})
        self.assertEqual(body["unanswered"], 3)
        self.assertEqual(body["per_machine"], [
            {"machine_id": "M-AC90", "model": "AC-90", "total": 2, "answered": 0, "insufficient_evidence": 0, "error": 1},
            {"machine_id": "M-HP200", "model": "HP-200", "total": 3, "answered": 2, "insufficient_evidence": 1, "error": 0},
        ])
        self.assertEqual(len(body["per_day"]), 7)
        self.assertEqual(body["per_day"][-1], {"date": timezone.localdate().isoformat(), "count": 3})
        self.assertEqual(body["per_day"][-3]["count"], 1)
        self.assertEqual(sum(d["count"] for d in body["per_day"]), 4)
        self.assertEqual({r["question"] for r in body["recent_unanswered"]},
                         {"hp insufficient_evidence", "ac error", "ac no answer"})
