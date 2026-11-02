"""Celery application for background batch inspections. Settings come from Django settings (CELERY_*).

Worker: ``celery -A backend.config worker --loglevel=info`` (Redis must be running, see CELERY_BROKER_URL).
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.config.settings")

app = Celery("industrix")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()  # backend/inspections/tasks.py
