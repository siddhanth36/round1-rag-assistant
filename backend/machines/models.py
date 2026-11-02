import uuid

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models


class Machine(models.Model):
    """A registered machine. Field names follow machine_manual_index.csv.

    ``machine_id`` is copied into every vector record by rag/, so it must never change after creation.
    """

    machine_id = models.CharField(max_length=32, unique=True, validators=[RegexValidator(r"^[A-Z0-9-]+$")])
    model = models.CharField(max_length=32, unique=True)
    machine_name = models.CharField(max_length=120)


def manual_upload_path(instance, filename: str) -> str:
    """Server-generated name: the uploader's filename and path never reach the filesystem."""
    return f"manuals/{uuid.uuid4().hex}.pdf"


class Manual(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        INDEXED = "indexed"
        FAILED = "failed"

    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="manuals")
    document_id = models.CharField(max_length=32, validators=[RegexValidator(r"^[A-Z0-9][A-Z0-9-]*$")])
    revision = models.CharField(max_length=16, validators=[RegexValidator(r"^[0-9A-Za-z][0-9A-Za-z.-]*$")])
    original_filename = models.CharField(max_length=255)  # display only (citations); never used as a path
    file = models.FileField(upload_to=manual_upload_path)
    page_count = models.PositiveSmallIntegerField()
    indexing_status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["document_id", "revision"], name="uniq_manual_doc_revision")]
