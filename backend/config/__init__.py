# Load the Celery app with Django so tasks bind to it and `celery -A backend.config` finds it.
from backend.config.celery import app as celery_app

__all__ = ["celery_app"]
