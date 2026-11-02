import logging
from pathlib import PurePath

import pymupdf
from django.conf import settings
from rest_framework import serializers

from backend.machines.models import Machine, Manual

logger = logging.getLogger(__name__)

class MachineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Machine
        fields = ["machine_id", "model", "machine_name"]


class ManualSerializer(serializers.ModelSerializer):
    machine_id = serializers.CharField(source="machine.machine_id", read_only=True)
    uploaded_by = serializers.CharField(source="uploaded_by.username", read_only=True)
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = Manual
        fields = ["id", "machine_id", "document_id", "revision", "original_filename", "page_count",
                  "indexing_status", "indexing_error", "chunk_count", "uploaded_by", "uploaded_at", "file_url"]

    def get_file_url(self, manual: Manual) -> str:
        return f"/api/manuals/{manual.id}/file/"


def pdf_page_count(upload) -> int:
    """Validate an uploaded PDF and return its page count. Cheap checks run before the file is read."""
    if not upload.name.lower().endswith(".pdf"):
        raise serializers.ValidationError("Only .pdf files are accepted.")
    if upload.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise serializers.ValidationError(f"The file is larger than the {settings.MAX_UPLOAD_MB} MB limit.")
    data = upload.read()
    upload.seek(0)
    if not data.startswith(b"%PDF-"):
        raise serializers.ValidationError("The file is not a PDF.")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            encrypted, pages = doc.needs_pass, doc.page_count
    except Exception:  # PyMuPDF raises several types for corrupt input; all mean "unreadable"
        raise serializers.ValidationError("The PDF could not be opened. It may be damaged.") from None
    if encrypted:
        raise serializers.ValidationError("Password-protected PDFs are not supported.")
    if pages < 1:
        raise serializers.ValidationError("The PDF has no pages.")
    return pages


class ManualUploadSerializer(serializers.ModelSerializer):
    """Used by POST /api/manuals/upload/ and by the load_manuals command (same validation path)."""

    machine_id = serializers.SlugRelatedField(slug_field="machine_id", queryset=Machine.objects.all(), source="machine")
    file = serializers.FileField()  # the model's max_length limits the stored path, not the upload's name

    class Meta:
        model = Manual
        fields = ["machine_id", "document_id", "revision", "file"]
        validators = []  # the unique (document_id, revision) rule is checked in validate() with a clearer message

    def validate(self, attrs):
        document_id, revision, machine = attrs["document_id"], attrs["revision"], attrs["machine"]
        if Manual.objects.filter(document_id=document_id, revision=revision).exists():
            raise serializers.ValidationError(f"{document_id} revision {revision} is already registered.")
        other = Manual.objects.filter(document_id=document_id).exclude(machine=machine).first()
        if other:  # rag/ also refuses one document_id on two machines
            raise serializers.ValidationError({"document_id": f"{document_id} belongs to {other.machine.machine_id}."})
        upload = attrs["file"]
        try:
            attrs["page_count"] = pdf_page_count(upload)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({"file": exc.detail}) from None
        attrs["original_filename"] = PurePath(upload.name.replace("\\", "/")).name[:255]
        return attrs

    def create(self, validated_data):
        manual = super().create(validated_data)
        request_indexing(manual)
        return manual


def request_indexing(manual: Manual) -> Manual:
    """Index a stored manual through rag (via the adapter) and record the outcome on the row."""
    from backend.assistant import rag_adapter  # deferred: keeps rag/chromadb out of every Django import

    try:
        _, summary = rag_adapter.index_manual(manual.file.path, machine_id=manual.machine.machine_id,
                                              document_id=manual.document_id, revision=manual.revision,
                                              source_filename=manual.original_filename)
    except Exception as exc:  # RagError or an unexpected failure: the manual must not look usable
        logger.exception("Indexing failed for %s rev %s", manual.document_id, manual.revision)
        manual.indexing_status, manual.indexing_error, manual.chunk_count = Manual.Status.FAILED, f"{type(exc).__name__}: {exc}"[:2000], None
    else:
        manual.indexing_status, manual.indexing_error, manual.chunk_count = Manual.Status.INDEXED, "", summary.chunks_indexed
    manual.save(update_fields=["indexing_status", "indexing_error", "chunk_count"])
    return manual
