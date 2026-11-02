"""Inspection business logic. Views and Celery tasks call these; nothing here knows about HTTP."""

import logging
from pathlib import PurePath

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from backend.inspections import ai_adapter
from backend.inspections.models import Inspection, InspectionBatch, InspectionReview, Product
from backend.inspections.validation import ImageRejected, validate_image

logger = logging.getLogger(__name__)

MAX_BATCH_FILES = 10
QUEUE_UNAVAILABLE = "Background worker queue unavailable (Redis not reachable)."
IMAGE_MISSING = "The stored image file is missing."
UNEXPECTED_FAILURE = "The inspection could not be processed."
WORKER_OFFLINE_WARNING = "No inspection worker is running: queued images will wait until one starts."
UNFINISHED = (Inspection.Status.QUEUED, Inspection.Status.PROCESSING)


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
    Raises ``Inspection.DoesNotExist`` for an unknown id.
    """
    rows = Inspection.objects.filter(pk=inspection_id, status__in=UNFINISHED)
    if not rows.update(status=Inspection.Status.PROCESSING, started_at=timezone.now()):
        return Inspection.objects.get(pk=inspection_id)  # already final: leave it untouched
    inspection = Inspection.objects.get(pk=inspection_id)
    processing = Inspection.objects.filter(pk=inspection_id, status=Inspection.Status.PROCESSING)
    if not inspection.image or not inspection.image.storage.exists(inspection.image.name):
        logger.error("Inspection %s: stored image %r is missing", inspection_id, inspection.image.name)
        processing.update(status=Inspection.Status.FAILED, failure_reason=IMAGE_MISSING, completed_at=timezone.now())
        inspection.refresh_from_db()
        return inspection
    try:
        result = ai_adapter.predict_image(inspection.image.path)
    except ai_adapter.InferenceError as exc:
        processing.update(status=Inspection.Status.FAILED, failure_reason=str(exc), completed_at=timezone.now())
    else:
        processing.update(status=Inspection.Status.COMPLETED, completed_at=timezone.now(), **result)
    inspection.refresh_from_db()
    return inspection



def fail_unfinished(inspection_ids, reason: str) -> int:
    """Mark still-unfinished inspections failed with ``reason``; final rows are never touched."""
    return Inspection.objects.filter(pk__in=list(inspection_ids), status__in=UNFINISHED).update(
        status=Inspection.Status.FAILED, failure_reason=reason, completed_at=timezone.now())


# --- batches ---------------------------------------------------------------------------------------

def create_batch(uploads, *, product: Product, user) -> InspectionBatch:
    """Save a batch: one Inspection per upload, in upload order. ``uploads`` must hold 1-10 files.

    Invalid files become ``failed`` items and are never stored or queued; valid files are stored ``queued``.
    One task per valid item is sent only after the transaction commits, so a worker never sees a missing row.
    If the broker can't be reached, the items that weren't sent become ``failed`` (QUEUE_UNAVAILABLE).
    """
    with transaction.atomic():
        batch = InspectionBatch.objects.create(product=product, submitted_by=user)
        items = [create_inspection(upload, product=product, user=user, batch=batch) for upload in uploads]
        queued = [item.pk for item in items if item.status == Inspection.Status.QUEUED]
        if queued:
            transaction.on_commit(lambda: enqueue_inspections(queued))
    return batch


def enqueue_inspections(inspection_ids: list[int]) -> None:
    """Send one Celery task per inspection. On the first broker failure, the rest are marked failed.

    Stopping at the first failure keeps the request fast while Redis is down (each attempt waits for a timeout).
    """
    from backend.inspections.tasks import process_inspection  # tasks imports this module

    for index, inspection_id in enumerate(inspection_ids):
        try:
            process_inspection.delay(inspection_id)
        except Exception:  # kombu OperationalError, redis/socket errors: the queue can't take the task
            unsent = inspection_ids[index:]
            logger.exception("Could not queue inspections %s; marking them failed", unsent)
            fail_unfinished(unsent, QUEUE_UNAVAILABLE)
            return


def batches_with_counts():
    """InspectionBatch rows annotated with per-status item counts (one query, computed in the database)."""
    return (InspectionBatch.objects.select_related("product", "submitted_by")
            .annotate(**{f"n_{value}": Count("items", filter=Q(items__status=value))
                         for value in Inspection.Status.values}))


def batch_counts(batch) -> dict:
    return {value: getattr(batch, f"n_{value}") for value in Inspection.Status.values}


def batch_done(counts: dict) -> bool:
    return not (counts[Inspection.Status.QUEUED] or counts[Inspection.Status.PROCESSING])


def worker_online() -> bool:
    """True if at least one Celery worker answers a ping (1 s timeout). Cached for 10 s per process."""
    online = cache.get("inspections:worker_online")
    if online is None:
        from backend.config.celery import app

        try:
            online = bool(app.control.ping(timeout=1.0))
        except Exception:  # broker unreachable: no worker can be reached either
            logger.warning("Celery worker ping failed", exc_info=True)
            online = False
        cache.set("inspections:worker_online", online, timeout=10)
    return online


# --- reviews ---------------------------------------------------------------------------------------

class NotReviewable(Exception):
    """The inspection has no completed prediction to review."""


def add_review(inspection_id: int, *, reviewer, decision: str, reviewed_class: str, comment: str) -> InspectionReview:
    """Append a supervisor review and make it current. The original prediction is never modified.

    The inspection row is locked so concurrent reviews are applied in order and ``review_status`` always
    follows the newest review. Raises ``NotReviewable`` unless the inspection is completed.
    """
    with transaction.atomic():
        inspection = Inspection.objects.select_for_update().get(pk=inspection_id)
        if inspection.status != Inspection.Status.COMPLETED:
            raise NotReviewable
        review = InspectionReview.objects.create(inspection=inspection, reviewer=reviewer, decision=decision,
                                                 reviewed_class=reviewed_class, comment=comment)
        Inspection.objects.filter(pk=inspection_id).update(review_status=decision)  # only this column changes
    return review
