"""Celery tasks: one task per batch item. All logic lives in services; this only adds retry/failure policy."""

import logging

from celery import shared_task
from django.db import DatabaseError

from backend.inspections import services
from backend.inspections.models import Inspection

logger = logging.getLogger(__name__)

DB_RETRIES = 3


@shared_task(bind=True, max_retries=DB_RETRIES, acks_late=True)
def process_inspection(self, inspection_id: int) -> str | None:
    """Run the frozen model for one queued inspection and store its original prediction.

    Idempotent: a redelivered or duplicate task never re-runs a finished item (services.record_prediction).
    Model errors and missing files are permanent: the item is marked failed and not retried. Only database
    errors (likely transient) are retried with backoff; after the last retry the item is marked failed.
    Returns the final status, or None for an unknown id.
    """
    try:
        return services.record_prediction(inspection_id).status
    except Inspection.DoesNotExist:
        logger.warning("Inspection %s no longer exists; task dropped", inspection_id)
        return None
    except DatabaseError as exc:
        if self.request.retries < DB_RETRIES:
            logger.warning("Database error for inspection %s, retrying: %s", inspection_id, exc)
            raise self.retry(exc=exc, countdown=2 ** self.request.retries) from exc
        logger.exception("Inspection %s failed after %s database retries", inspection_id, DB_RETRIES)
        services.fail_unfinished([inspection_id], services.UNEXPECTED_FAILURE)
        raise
    except Exception:
        logger.exception("Unexpected error processing inspection %s", inspection_id)
        services.fail_unfinished([inspection_id], services.UNEXPECTED_FAILURE)
        raise
