"""Inspection business logic. Views and Celery tasks call these; nothing here knows about HTTP."""

from pathlib import PurePath

from django.utils import timezone

from backend.inspections import ai_adapter
from backend.inspections.models import Inspection, Product
from backend.inspections.validation import ImageRejected, validate_image


def display_name(upload) -> str:
    """The client's filename, for display only (it never reaches the filesystem or the model)."""
    return PurePath((upload.name or "").replace("\\", "/")).name[:255] or "unnamed"


def create_inspection(upload, *, product: Product, user, batch=None) -> Inspection:
    """Validate ``upload`` and save an Inspection row.

    Invalid file: a ``failed`` row with the readable reason and no stored file; the model is never called.
    Valid file: stored under a server-generated name, row ``queued`` and ready for ``record_prediction``.
    """
    inspection = Inspection(product=product, submitted_by=user, batch=batch, original_filename=display_name(upload))
    try:
        image = validate_image(upload)
    except ImageRejected as exc:
        inspection.status, inspection.failure_reason, inspection.completed_at = (
            Inspection.Status.FAILED, str(exc), timezone.now())
        inspection.file_size = upload.size or 0
        inspection.save()
        return inspection
    inspection.image_format, inspection.width, inspection.height = image.format, image.width, image.height
    inspection.file_size, inspection.sha256 = image.size, image.sha256
    inspection.image.save(f"image{image.extension}", upload, save=False)  # name from upload_to, not the client
    inspection.save()
    return inspection


def record_prediction(inspection_id: int) -> Inspection:
    """Run the model once and write the original prediction. Never overwrites a completed or failed row.

    Each transition is a conditional UPDATE, so a duplicate call (e.g. a redelivered Celery task) is a no-op.
    """
    rows = Inspection.objects.filter(pk=inspection_id,
                                     status__in=(Inspection.Status.QUEUED, Inspection.Status.PROCESSING))
    if not rows.update(status=Inspection.Status.PROCESSING, started_at=timezone.now()):
        return Inspection.objects.get(pk=inspection_id)  # already final: leave it untouched
    inspection = Inspection.objects.get(pk=inspection_id)
    processing = Inspection.objects.filter(pk=inspection_id, status=Inspection.Status.PROCESSING)
    try:
        result = ai_adapter.predict_image(inspection.image.path)
    except ai_adapter.InferenceError as exc:
        processing.update(status=Inspection.Status.FAILED, failure_reason=str(exc), completed_at=timezone.now())
    else:
        processing.update(status=Inspection.Status.COMPLETED, completed_at=timezone.now(), **result)
    inspection.refresh_from_db()
    return inspection

