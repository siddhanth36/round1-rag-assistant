"""Products, single inspection, detail and protected image. Every prediction comes from the real supplied model."""

import importlib.util
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from backend.accounts.models import User
from backend.inspections import ai_adapter
from backend.inspections.models import Inspection, Product

PACK = settings.BASE_DIR / "challenge-pack-r2"
SAMPLE = PACK / "Samples" / "images" / "inspection_001.png"
ORIGINAL_FIELDS = ("status", "predicted_class", "confidence", "defect_probability", "model_version",
                   "image", "sha256", "submitted_by_id", "product_id", "created_at", "completed_at")


def supplied_predict(path: Path) -> dict:
    """Call the supplied inference.py directly, independently of ai_adapter, as the reference."""
    spec = importlib.util.spec_from_file_location("reference_inference", PACK / "Model" / "inference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.predict(str(path))


def release(response) -> bytes:
    """Consume a streamed response so the test client closes its file (Windows can't delete open files).

    Calling response.close() directly would fire request_finished and close the test's DB connection.
    """
    return b"".join(response.streaming_content) if response.streaming else b""


class InspectionTestCase(TestCase):
    """Isolated MEDIA_ROOT; one user per role plus engineer2; product P-PLATE01."""

    def setUp(self):
        self.media = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(override_settings(MEDIA_ROOT=self.media))
        self.users = {role: User.objects.create_user(username=role.lower(), password="x", role=role)
                      for role in User.Role.values}
        self.users["ENGINEER2"] = User.objects.create_user(username="engineer2", password="x",
                                                           role=User.Role.ENGINEER)
        Product.objects.create(product_id="P-PLATE01", product_name="Demonstration metal plate")
        self.client = APIClient()

    def as_role(self, role):
        self.client.force_authenticate(self.users[role])

    def inspect(self, path: Path = SAMPLE, name: str | None = None, product_id: str = "P-PLATE01"):
        image = SimpleUploadedFile(name or path.name, path.read_bytes(), "application/octet-stream")
        return self.client.post("/api/inspections/", {"product_id": product_id, "image": image}, format="multipart")

    def stored_files(self):
        return [p for p in self.media.rglob("*") if p.is_file()]


class SingleInspectionTests(InspectionTestCase):
    def test_engineer_upload_is_completed_with_the_supplied_models_output(self):
        self.as_role("ENGINEER")
        response = self.inspect()
        self.assertEqual(response.status_code, 201, response.data)
        expected = supplied_predict(SAMPLE)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["original"], expected)
        self.assertEqual(response.data["original"]["model_version"], "BIT-PLATE-CV-1.0")
        self.assertEqual(response.data["submitted_by"], "engineer")
        self.assertEqual((response.data["width"], response.data["height"], response.data["image_format"]),
                         (256, 256, "PNG"))
        row = Inspection.objects.get(pk=response.data["id"])
        self.assertEqual((row.predicted_class, row.confidence, row.defect_probability),
                         (expected["predicted_class"], expected["confidence"], expected["defect_probability"]))
        self.assertIsNotNone(row.completed_at)
        stored = self.stored_files()
        self.assertEqual(len(stored), 1)
        self.assertRegex(stored[0].name, r"^[0-9a-f]{32}\.png$")  # server-generated name

    def test_submitted_by_comes_from_the_session_not_the_body(self):
        self.as_role("ENGINEER")
        image = SimpleUploadedFile("a.png", SAMPLE.read_bytes())
        response = self.client.post("/api/inspections/", {"product_id": "P-PLATE01", "image": image,
                                                          "submitted_by": self.users["SUPERVISOR"].id})
        self.assertEqual(Inspection.objects.get(pk=response.data["id"]).submitted_by, self.users["ENGINEER"])

    def test_validation_files_are_saved_as_failed_and_never_reach_the_model(self):
        self.as_role("ENGINEER")
        reasons = {"not_an_image.jpg": "not a JPEG or PNG", "corrupt_image.png": "corrupt or truncated",
                   "too_small.png": "32×32 px"}
        with mock.patch("backend.inspections.ai_adapter.predict_image", wraps=ai_adapter.predict_image) as spy:
            for name, reason in reasons.items():
                with self.subTest(name):
                    response = self.inspect(PACK / "Validation" / name)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(set(response.data), {"id", "status", "failure_reason"})
                    self.assertEqual(response.data["status"], "failed")
                    self.assertIn(reason, response.data["failure_reason"])
                    row = Inspection.objects.get(pk=response.data["id"])
                    self.assertEqual((row.status, row.predicted_class, row.confidence, row.model_version),
                                     ("failed", None, None, None))
                    self.assertFalse(row.image)
        spy.assert_not_called()
        self.assertEqual(self.stored_files(), [])  # invalid bytes are never stored

    def test_renamed_files_give_the_same_prediction(self):
        self.as_role("ENGINEER")
        expected = supplied_predict(SAMPLE)
        jpeg = PACK / "Samples" / "images" / "inspection_002.jpg"
        for path, name, fmt in [(SAMPLE, "photo.jpg", "PNG"), (SAMPLE, "random.bin.png", "PNG"),
                                (jpeg, "defect_scratch.png", "JPEG")]:
            with self.subTest(name):
                response = self.inspect(path, name=name)
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.data["image_format"], fmt)
                self.assertEqual(response.data["original"], expected if path == SAMPLE else supplied_predict(jpeg))
                self.assertEqual(response.data["original_filename"], name)

    def test_result_persists_and_is_identical_in_a_new_request(self):
        self.as_role("ENGINEER")
        created = self.inspect().data
        before = Inspection.objects.values(*ORIGINAL_FIELDS).get(pk=created["id"])
        fresh = APIClient()
        fresh.login(username="engineer", password="x")  # new session, real login
        detail = fresh.get(f"/api/inspections/{created['id']}/").data
        self.assertEqual(detail["original"], created["original"])
        self.assertEqual(detail["created_at"], created["created_at"])
        self.assertEqual(Inspection.objects.values(*ORIGINAL_FIELDS).get(pk=created["id"]), before)

    def test_inference_error_is_saved_as_failed_without_a_prediction(self):
        self.as_role("ENGINEER")
        broken = mock.Mock(side_effect=RuntimeError("weights unreadable"))
        with mock.patch("backend.inspections.ai_adapter._model_predict", return_value=broken):
            response = self.inspect()
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.data["status"], "failed")
        self.assertEqual(response.data["failure_reason"], "The model could not process this image.")
        self.assertIsNone(response.data["original"])
        row = Inspection.objects.get(pk=response.data["id"])
        self.assertEqual((row.predicted_class, row.confidence, row.defect_probability, row.model_version),
                         (None, None, None, None))

    def test_completed_prediction_is_never_overwritten(self):
        from backend.inspections.services import record_prediction

        self.as_role("ENGINEER")
        pk = self.inspect().data["id"]
        before = Inspection.objects.values(*ORIGINAL_FIELDS).get(pk=pk)
        with mock.patch("backend.inspections.ai_adapter.predict_image") as model:
            record_prediction(pk)  # e.g. a redelivered task
        model.assert_not_called()
        self.assertEqual(Inspection.objects.values(*ORIGINAL_FIELDS).get(pk=pk), before)

    def test_product_must_exist_and_be_active(self):
        self.as_role("ENGINEER")
        Product.objects.create(product_id="P-OLD", product_name="Retired", is_active=False)
        for product_id in ["P-NOPE", "P-OLD", ""]:
            with self.subTest(product_id):
                self.assertEqual(self.inspect(product_id=product_id).status_code, 400)
        self.assertEqual(Inspection.objects.count(), 0)

    def test_missing_image_is_400(self):
        self.as_role("ENGINEER")
        response = self.client.post("/api/inspections/", {"product_id": "P-PLATE01"}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("image", response.data)


class InspectionPermissionTests(InspectionTestCase):
    def test_supervisor_cannot_submit_and_admin_can(self):
        self.as_role("SUPERVISOR")
        self.assertEqual(self.inspect().status_code, 403)
        self.assertEqual(Inspection.objects.count(), 0)
        self.as_role("ADMIN")
        self.assertEqual(self.inspect().status_code, 201)

    def test_anonymous_cannot_submit(self):
        self.assertEqual(self.inspect().status_code, 401)

    def test_other_engineer_gets_404_and_supervisor_and_admin_see_it(self):
        self.as_role("ENGINEER")
        pk = self.inspect().data["id"]
        for role, expected in [("ENGINEER2", 404), ("SUPERVISOR", 200), ("ADMIN", 200), ("ENGINEER", 200)]:
            self.as_role(role)
            with self.subTest(role):
                self.assertEqual(self.client.get(f"/api/inspections/{pk}/").status_code, expected)
                image = self.client.get(f"/api/inspections/{pk}/image/")
                release(image)
                self.assertEqual(image.status_code, expected)

    def test_image_endpoint_requires_login_and_streams_the_stored_bytes(self):
        self.as_role("ENGINEER")
        pk = self.inspect().data["id"]
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(f"/api/inspections/{pk}/image/").status_code, 401)
        self.assertEqual(self.client.get(f"/api/inspections/{pk}/").status_code, 401)
        self.as_role("SUPERVISOR")
        response = self.client.get(f"/api/inspections/{pk}/image/")
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(release(response), SAMPLE.read_bytes())

    def test_failed_inspection_has_no_image(self):
        self.as_role("ENGINEER")
        pk = self.inspect(PACK / "Validation" / "too_small.png").data["id"]
        self.assertEqual(self.client.get(f"/api/inspections/{pk}/image/").status_code, 404)
        self.assertIsNone(self.client.get(f"/api/inspections/{pk}/").data["image_url"])

    def test_detail_shape_has_empty_reviews(self):
        self.as_role("ENGINEER")
        pk = self.inspect().data["id"]
        data = self.client.get(f"/api/inspections/{pk}/").data
        self.assertEqual((data["reviews"], data["current_review"], data["review_status"]), ([], None, "unreviewed"))
        self.assertEqual(data["image_url"], f"/api/inspections/{pk}/image/")


class ProductApiTests(InspectionTestCase):
    def test_any_role_lists_active_products(self):
        Product.objects.create(product_id="P-OLD", product_name="Retired", is_active=False)
        for role in ["ENGINEER", "SUPERVISOR", "ADMIN"]:
            self.as_role(role)
            with self.subTest(role):
                self.assertEqual([p["product_id"] for p in self.client.get("/api/products/").data], ["P-PLATE01"])

    def test_only_admin_creates_products(self):
        body = {"product_id": "P-PLATE02", "product_name": "Second plate"}
        for role in ["ENGINEER", "SUPERVISOR"]:
            self.as_role(role)
            with self.subTest(role):
                self.assertEqual(self.client.post("/api/products/", body, format="json").status_code, 403)
        self.assertFalse(Product.objects.filter(product_id="P-PLATE02").exists())
        self.as_role("ADMIN")
        self.assertEqual(self.client.post("/api/products/", body, format="json").status_code, 201)
        self.assertEqual(self.client.post("/api/products/", body, format="json").status_code, 400)  # duplicate

    def test_only_admin_patches_and_product_id_is_immutable(self):
        url = "/api/products/P-PLATE01/"
        for role in ["ENGINEER", "SUPERVISOR"]:
            self.as_role(role)
            with self.subTest(role):
                self.assertEqual(self.client.patch(url, {"is_active": False}, format="json").status_code, 403)
        self.as_role("ADMIN")
        response = self.client.patch(url, {"product_name": "Plate", "product_id": "P-HACK"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Product.objects.get().product_id, "P-PLATE01")
        self.assertEqual(Product.objects.get().product_name, "Plate")


class AdapterOutputCheckTests(SimpleTestCase):
    def predict_with(self, output):
        with mock.patch("backend.inspections.ai_adapter._model_predict", return_value=lambda path: output):
            return ai_adapter.predict_image("unused.png")

    def test_valid_output_passes_through_with_exactly_four_keys(self):
        good = {"predicted_class": "Normal", "confidence": 0.8, "defect_probability": 0.2,
                "model_version": "BIT-PLATE-CV-1.0", "extra": 1}
        self.assertEqual(set(self.predict_with(good)), set(ai_adapter.KEYS))

    def test_invalid_outputs_raise_inference_error(self):
        base = {"predicted_class": "Normal", "confidence": 0.8, "defect_probability": 0.2, "model_version": "v"}
        for change in [{"predicted_class": "Scratched"}, {"confidence": 1.5}, {"defect_probability": -0.1},
                       {"confidence": float("nan")}, {"model_version": ""}, {"confidence": "high"}]:
            with self.subTest(change):
                with self.assertRaises(ai_adapter.InferenceError):
                    self.predict_with(base | change)
        with self.assertRaises(ai_adapter.InferenceError):
            self.predict_with({"predicted_class": "Normal"})  # missing keys
