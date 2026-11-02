from django.conf import settings
from django.db import models

from backend.machines.models import Machine, Manual


class Question(models.Model):
    """Saved before RAG runs, so a failed request is still logged."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="questions")
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="questions")
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class Answer(models.Model):
    """Written for every outcome, errors included. "Unanswered" = status != answered, or no Answer row."""

    class Status(models.TextChoices):
        ANSWERED = "answered"
        INSUFFICIENT_EVIDENCE = "insufficient_evidence"
        ERROR = "error"

    question = models.OneToOneField(Question, on_delete=models.CASCADE, related_name="answer")
    text = models.TextField()
    status = models.CharField(max_length=24, choices=Status.choices, db_index=True)
    retrieval_trace = models.JSONField(default=dict, blank=True)  # staff-only diagnostics, never shown to engineers
    latency_ms = models.PositiveIntegerField(default=0)


class Citation(models.Model):
    """Copied verbatim from the rag Citation; the passage is a snapshot so history survives re-indexing."""

    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name="citations")
    manual = models.ForeignKey(Manual, on_delete=models.PROTECT, related_name="citations")
    document_id = models.CharField(max_length=32)
    revision = models.CharField(max_length=16)
    pdf_page = models.PositiveSmallIntegerField()
    passage = models.TextField()
    source_tag = models.CharField(max_length=8)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["answer", "source_tag"], name="uniq_citation_tag")]
