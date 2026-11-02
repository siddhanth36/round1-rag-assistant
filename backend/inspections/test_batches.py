"""R2-4 batch upload, Celery task and batch tracking. The broker is mocked here (test_worker.py uses real Redis)."""

from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from kombu.exceptions import OperationalError

from backend.inspections import services, tasks
from backend.inspections.models import Inspection, InspectionBatch, Product
from backend.inspections.test_api import ORIGINAL_FIELDS, PACK, SAMPLE, InspectionTestCase, supplied_predict

IMAGES = PACK / "Samples" / "images"
VALID = [IMAGES / f"inspection_{n:03d}.{'png' if n % 2 else 'jpg'}" for n in range(1, 12)]
TOO_SMALL, NOT_IMAGE, CORRUPT = (PACK / "Validation" / n for n in ("too_small.png", "not_an_image.jpg",
                                                                   "corrupt_image.png"))
DELAY = "backend.inspections.tasks.process_inspection.delay"
PING = "backend.config.celery.app.control.ping"


class BatchTestCase(InspectionTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()  # worker health is cached for 10 s
        self.delay = self.enterContext(mock.patch(DELAY))
        self.ping = self.enterContext(mock.patch(PING, return_value=[{"celery@test": {"ok": "pong"}}]))

    def upload(self, paths: list[Path], product_id: str = "P-PLATE01"):
        files = [SimpleUploadedFile(p.name, p.read_bytes(), "application/octet-stream") for p in paths]
        with self.captureOnCommitCallbacks(execute=True):  # tasks are sent only after the commit
            return self.client.post("/api/inspections/batches/", {"product_id": product_id, "images": files},
                                    format="multipart")

    def queued_ids(self) -> list[int]:
        return [c.args[0] for c in self.delay.call_args_list]


class BatchUploadTests(BatchTestCase):
    def test_one_valid_file_is_queued_after_commit(self):
        self.as_role("ENGINEER")
        response = self.upload([SAMPLE])
        self.assertEqual(response.status_code, 202, response.data)
        batch = InspectionBatch.objects.get()
        self.assertEqual(response["Location"], f"/api/inspections/batches/{batch.id}/")
        self.assertEqual(response.data["batch_id"], batch.id)
        self.assertEqual(response.data["submitted_by"], "engineer")
        self.assertEqual(response.data["product"], "P-PLATE01")
        self.assertEqual(response.data["counts"], {"queued": 1, "processing": 0, "completed": 0, "failed": 0})
        self.assertFalse(response.data["done"])
        self.assertIs(response.data["worker_online"], True)
        self.assertIsNone(response.data["warning"])
        [item] = response.data["items"]
        self.assertEqual((item["status"], item["original"], item["original_filename"]), ("queued", None, SAMPLE.name))
        self.assertEqual(item["image_url"], f"/api/inspections/{item['id']}/image/")
        self.assertEqual(self.queued_ids(), [item["id"]])
        self.assertEqual(len(self.stored_files()), 1)

    def test_ten_valid_files_keep_upload_order_and_send_one_task_each(self):
        self.as_role("ADMIN")
        paths = VALID[:10][::-1]  # reversed, so order can't come from names
        response = self.upload(paths)
        self.assertEqual(response.status_code, 202, response.data)
        self.assertEqual([i["original_filename"] for i in response.data["items"]], [p.name for p in paths])
        self.assertEqual(response.data["counts"]["queued"], 10)
        self.assertEqual(self.queued_ids(), [i["id"] for i in response.data["items"]])
        detail = self.client.get(response["Location"]).data
        self.assertEqual([i["original_filename"] for i in detail["items"]], [p.name for p in paths])

    def test_zero_or_eleven_files_save_nothing(self):
        self.as_role("ENGINEER")
        for paths, count in [([], 0), (VALID, 11)]:
            with self.subTest(count=count):
                response = self.upload(paths, product_id="P-NOPE")  # the count is checked before the product
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {"images": [f"A batch must contain 1 to 10 images; {count} received."]})
        self.assertFalse(InspectionBatch.objects.exists())
        self.assertFalse(Inspection.objects.exists())
        self.assertEqual(self.stored_files(), [])
        self.delay.assert_not_called()

    def test_unknown_or_inactive_product_saves_nothing(self):
        self.as_role("ENGINEER")
        Product.objects.create(product_id="P-OLD", product_name="Retired", is_active=False)
        for product_id in ["P-NOPE", "P-OLD", ""]:
            with self.subTest(product_id=product_id):
                response = self.upload([SAMPLE], product_id=product_id)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data, {"product_id": ["Select an active product."]})
        self.assertFalse(InspectionBatch.objects.exists())
        self.assertFalse(Inspection.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_invalid_files_fail_individually_and_never_stop_valid_ones(self):
        self.as_role("ENGINEER")
        paths = [VALID[0], TOO_SMALL, VALID[1], NOT_IMAGE, CORRUPT]
        response = self.upload(paths)
        self.assertEqual(response.status_code, 202, response.data)
        items = response.data["items"]
        self.assertEqual([i["status"] for i in items], ["queued", "failed", "queued", "failed", "failed"])
        self.assertEqual(items[1]["failure_reason"], "The image is 32×32 px; each side must be 64–4096 px.")
        self.assertEqual(items[3]["failure_reason"], "The file content is not a JPEG or PNG image.")
        self.assertEqual(items[4]["failure_reason"], "The image data is corrupt or truncated.")
        for item in (items[1], items[3], items[4]):
            self.assertIsNone(item["image_url"])
            self.assertIsNone(item["original"])
            self.assertIsNotNone(item["completed_at"])
        self.assertEqual(self.queued_ids(), [items[0]["id"], items[2]["id"]])  # invalid files never queued
        self.assertEqual(len(self.stored_files()), 2)  # invalid files never stored
        self.assertEqual(response.data["counts"], {"queued": 2, "processing": 0, "completed": 0, "failed": 3})

    def test_all_invalid_files_make_a_finished_batch(self):
        self.as_role("ENGINEER")
        response = self.upload([TOO_SMALL, NOT_IMAGE])
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["counts"], {"queued": 0, "processing": 0, "completed": 0, "failed": 2})
        self.assertTrue(response.data["done"])
        self.assertIsNone(response.data["worker_online"])
        self.assertIsNone(response.data["warning"])
        self.delay.assert_not_called()
        self.ping.assert_not_called()  # no health check once done

    def test_redis_unavailable_fails_unsent_items_and_keeps_the_batch(self):
        self.as_role("ENGINEER")
        self.delay.side_effect = OperationalError("Error 111 connecting to localhost:6379. Connection refused.")
        response = self.upload([VALID[0], TOO_SMALL, VALID[1]])
        self.assertEqual(response.status_code, 202, response.data)
        # The test's on-commit callbacks run after the response; the real ordering is covered in test_worker.py.
        detail = self.client.get(response["Location"]).data
        items = detail["items"]
        self.assertEqual([i["status"] for i in items], ["failed", "failed", "failed"])
        self.assertEqual(items[0]["failure_reason"], "Background worker queue unavailable (Redis not reachable).")
        self.assertEqual(items[2]["failure_reason"], "Background worker queue unavailable (Redis not reachable).")
        self.assertEqual(items[1]["failure_reason"], "The image is 32×32 px; each side must be 64–4096 px.")
        self.assertTrue(detail["done"])
        self.assertEqual(InspectionBatch.objects.count(), 1)
        self.assertEqual(Inspection.objects.count(), 3)
        self.assertEqual(self.delay.call_count, 1)  # stops at the first broker failure

    def test_redis_failing_midway_keeps_already_queued_items(self):
        self.as_role("ENGINEER")
        self.delay.side_effect = [None, OperationalError("connection lost")]
        response = self.upload(VALID[:3])
        items = self.client.get(response["Location"]).data["items"]
        self.assertEqual([i["status"] for i in items], ["queued", "failed", "failed"])

    def test_submitter_is_the_session_user(self):
        self.as_role("ENGINEER")
        files = [SimpleUploadedFile(SAMPLE.name, SAMPLE.read_bytes())]
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/inspections/batches/", {
                "product_id": "P-PLATE01", "images": files, "submitted_by": self.users["ADMIN"].id,
                "role": "ADMIN"}, format="multipart")
        self.assertEqual(response.data["submitted_by"], "engineer")
        self.assertEqual(Inspection.objects.get().submitted_by, self.users["ENGINEER"])


class BatchPermissionTests(BatchTestCase):
    def test_supervisor_cannot_upload(self):
        self.as_role("SUPERVISOR")
        response = self.upload([SAMPLE])
        self.assertEqual(response.status_code, 403)
        self.assertFalse(InspectionBatch.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_anonymous_gets_401_everywhere(self):
        self.assertEqual(self.upload([SAMPLE]).status_code, 401)
        self.assertEqual(self.client.get("/api/inspections/batches/").status_code, 401)
        self.assertEqual(self.client.get("/api/inspections/batches/1/").status_code, 401)

    def test_engineers_see_only_their_batches_supervisor_and_admin_see_all(self):
        self.as_role("ENGINEER")
        mine = self.upload([SAMPLE]).data["batch_id"]
        self.as_role("ENGINEER2")
        theirs = self.upload([SAMPLE]).data["batch_id"]
        self.assertEqual([b["batch_id"] for b in self.client.get("/api/inspections/batches/").data], [theirs])
        self.assertEqual(self.client.get(f"/api/inspections/batches/{mine}/").status_code, 404)
        self.as_role("ENGINEER")
        self.assertEqual([b["batch_id"] for b in self.client.get("/api/inspections/batches/").data], [mine])
        for role in ("SUPERVISOR", "ADMIN"):
            with self.subTest(role=role):
                self.as_role(role)
                listed = self.client.get("/api/inspections/batches/").data
                self.assertEqual([b["batch_id"] for b in listed], [theirs, mine])  # newest first
                self.assertEqual(self.client.get(f"/api/inspections/batches/{mine}/").status_code, 200)


class BatchTrackingTests(BatchTestCase):
    def test_counts_and_done_follow_the_items(self):
        self.as_role("ENGINEER")
        batch = self.upload([VALID[0], VALID[1], TOO_SMALL]).data
        first, second = (i["id"] for i in batch["items"][:2])
        tasks.process_inspection(first)
        detail = self.client.get(f"/api/inspections/batches/{batch['batch_id']}/").data
        self.assertEqual(detail["counts"], {"queued": 1, "processing": 0, "completed": 1, "failed": 1})
        self.assertFalse(detail["done"])
        Inspection.objects.filter(pk=second).update(status="processing")
        detail = self.client.get(f"/api/inspections/batches/{batch['batch_id']}/").data
        self.assertEqual(detail["counts"], {"queued": 0, "processing": 1, "completed": 1, "failed": 1})
        self.assertFalse(detail["done"])
        tasks.process_inspection(second)
        detail = self.client.get(f"/api/inspections/batches/{batch['batch_id']}/").data
        self.assertEqual(detail["counts"], {"queued": 0, "processing": 0, "completed": 2, "failed": 1})
        self.assertTrue(detail["done"])
        self.assertIsNone(detail["worker_online"])
        listed = self.client.get("/api/inspections/batches/").data[0]
        self.assertEqual((listed["counts"], listed["done"]), (detail["counts"], True))
        self.assertEqual(set(listed), {"batch_id", "product", "submitted_by", "created_at", "counts", "done"})

    def test_worker_offline_gives_false_and_the_contract_warning(self):
        self.as_role("ENGINEER")
        self.ping.return_value = []
        data = self.upload([SAMPLE]).data
        self.assertIs(data["worker_online"], False)
        self.assertEqual(data["warning"], "No inspection worker is running: queued images will wait until one starts.")

    def test_worker_ping_failure_counts_as_offline(self):
        self.as_role("ENGINEER")
        self.ping.side_effect = OperationalError("Redis down")
        data = self.upload([SAMPLE]).data
        self.assertIs(data["worker_online"], False)
        self.assertIsNotNone(data["warning"])

    def test_worker_health_is_cached_and_pinged_with_a_one_second_timeout(self):
        self.as_role("ENGINEER")
        batch_id = self.upload([SAMPLE]).data["batch_id"]
        for _ in range(3):
            self.client.get(f"/api/inspections/batches/{batch_id}/")
        self.ping.assert_called_once_with(timeout=1.0)

    def test_no_warning_when_nothing_is_queued(self):
        self.as_role("ENGINEER")
        self.ping.return_value = []
        batch = self.upload([SAMPLE]).data
        Inspection.objects.filter(pk=batch["items"][0]["id"]).update(status="processing")
        detail = self.client.get(f"/api/inspections/batches/{batch['batch_id']}/").data
        self.assertIs(detail["worker_online"], False)
        self.assertIsNone(detail["warning"])  # contract: only while items are still queued


class ProcessInspectionTaskTests(BatchTestCase):
    def queued(self, path: Path = SAMPLE) -> int:
        self.as_role("ENGINEER")
        return self.upload([path]).data["items"][0]["id"]

    def test_task_stores_the_frozen_models_output_unchanged(self):
        pk = self.queued()
        self.assertEqual(tasks.process_inspection(pk), "completed")
        detail = self.client.get(f"/api/inspections/{pk}/").data
        self.assertEqual(detail["original"], supplied_predict(SAMPLE))
        self.assertEqual(detail["batch"], InspectionBatch.objects.get().id)
        self.assertIsNotNone(detail["started_at"])
        self.assertIsNotNone(detail["completed_at"])

    def test_duplicate_delivery_never_reruns_or_overwrites(self):
        pk = self.queued()
        tasks.process_inspection(pk)
        before = Inspection.objects.values(*ORIGINAL_FIELDS).get(pk=pk)
        with mock.patch("backend.inspections.ai_adapter.predict_image") as model:
            self.assertEqual(tasks.process_inspection(pk), "completed")
        model.assert_not_called()
        self.assertEqual(Inspection.objects.values(*ORIGINAL_FIELDS).get(pk=pk), before)
        self.assertEqual(Inspection.objects.count(), 1)

    def test_redelivery_after_a_crash_mid_processing_completes_the_item(self):
        pk = self.queued()
        Inspection.objects.filter(pk=pk).update(status="processing")  # worker died before finishing
        self.assertEqual(tasks.process_inspection(pk), "completed")

    def test_model_error_fails_the_item_without_a_prediction(self):
        pk = self.queued()
        with mock.patch("inspection_ai.predict_image", side_effect=RuntimeError("weights unreadable")):
            self.assertEqual(tasks.process_inspection(pk), "failed")
        row = Inspection.objects.get(pk=pk)
        self.assertEqual(row.failure_reason, "The model could not process this image.")
        self.assertEqual((row.predicted_class, row.confidence, row.model_version), (None, None, None))

    def test_missing_image_file_fails_the_item_and_never_calls_the_model(self):
        pk = self.queued()
        Path(Inspection.objects.get(pk=pk).image.path).unlink()
        with mock.patch("backend.inspections.ai_adapter.predict_image") as model:
            self.assertEqual(tasks.process_inspection(pk), "failed")
        model.assert_not_called()
        self.assertEqual(Inspection.objects.get(pk=pk).failure_reason, "The stored image file is missing.")

    def test_unexpected_error_marks_the_item_failed_and_is_reraised(self):
        pk = self.queued()
        with mock.patch.object(services, "record_prediction", side_effect=RuntimeError("bug")):
            with self.assertRaises(RuntimeError):
                tasks.process_inspection(pk)
        row = Inspection.objects.get(pk=pk)
        self.assertEqual((row.status, row.failure_reason), ("failed", "The inspection could not be processed."))

    def test_unknown_inspection_is_dropped(self):
        self.assertIsNone(tasks.process_inspection(987654321))

    def test_task_options_are_safe_for_redelivery(self):
        self.assertTrue(tasks.process_inspection.acks_late)
        self.assertEqual(tasks.process_inspection.max_retries, tasks.DB_RETRIES)
