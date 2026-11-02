"""Batch flow with real commits and a real broker (no mocks): needs TransactionTestCase so on_commit fires.

Each test runs only in the matching environment and says why it skipped otherwise:
- Redis down (nothing on CELERY_BROKER_URL): the 202 body already shows the queue failure.
- Redis up: a real in-process Celery worker processes the batch end to end.
"""

import socket
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

from celery.contrib.testing.worker import start_worker
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APIClient

from backend.accounts.models import User
from backend.config.celery import app
from backend.inspections.models import Product
from backend.inspections.test_api import supplied_predict
from backend.inspections.test_batches import TOO_SMALL, VALID


def redis_reachable() -> bool:
    url = urlparse(settings.CELERY_BROKER_URL)
    try:
        socket.create_connection((url.hostname or "localhost", url.port or 6379), timeout=1).close()
        return True
    except OSError:
        return False


class RealBrokerBatchTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        media = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(override_settings(MEDIA_ROOT=media))
        Product.objects.create(product_id="P-PLATE01", product_name="Demonstration metal plate")
        self.client = APIClient()
        self.client.force_authenticate(User.objects.create_user(username="engineer", password="x",
                                                                role=User.Role.ENGINEER))

    def upload(self, paths):
        files = [SimpleUploadedFile(p.name, p.read_bytes()) for p in paths]
        return self.client.post("/api/inspections/batches/", {"product_id": "P-PLATE01", "images": files},
                                format="multipart")

    def test_redis_down_202_body_already_shows_the_failure(self):
        if redis_reachable():
            raise unittest.SkipTest("Redis is running; this test needs it unreachable")
        response = self.upload([VALID[0], TOO_SMALL, VALID[1]])
        self.assertEqual(response.status_code, 202, response.data)
        reasons = [i["failure_reason"] for i in response.data["items"]]
        self.assertEqual(reasons, ["Background worker queue unavailable (Redis not reachable).",
                                   "The image is 32×32 px; each side must be 64–4096 px.",
                                   "Background worker queue unavailable (Redis not reachable)."])
        self.assertTrue(response.data["done"])
        self.assertIsNone(response.data["worker_online"])

    def test_real_worker_processes_the_batch(self):
        if not redis_reachable():
            raise unittest.SkipTest(f"Redis not reachable at {settings.CELERY_BROKER_URL}")
        with start_worker(app, pool="threads", concurrency=2, perform_ping_check=False, shutdown_timeout=10):
            response = self.upload([VALID[0], TOO_SMALL, VALID[1]])
            self.assertEqual(response.status_code, 202, response.data)
            deadline, detail = time.monotonic() + 60, response.data
            while not detail["done"] and time.monotonic() < deadline:
                time.sleep(0.5)
                detail = self.client.get(response["Location"]).data
                if not detail["done"]:
                    self.assertIs(detail["worker_online"], True)  # a real ping answered by the worker
        self.assertTrue(detail["done"], detail)
        self.assertEqual(detail["counts"], {"queued": 0, "processing": 0, "completed": 2, "failed": 1})
        self.assertIsNone(detail["worker_online"])
        items = detail["items"]
        self.assertEqual(items[0]["original"], supplied_predict(VALID[0]))
        self.assertEqual(items[2]["original"], supplied_predict(VALID[1]))
        self.assertEqual(items[1]["failure_reason"], "The image is 32×32 px; each side must be 64–4096 px.")
