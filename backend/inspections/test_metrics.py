"""R2-6: `manage.py evaluate_samples` and GET /api/inspections/metrics/. Real model unless a test says otherwise."""

import csv
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import override_settings

from backend.inspections import validation
from backend.inspections.evaluation import LIMITATIONS
from backend.inspections.models import Inspection, InspectionBatch, InspectionReview
from backend.inspections.test_api import PACK, InspectionTestCase

SAMPLES = PACK / "Samples"
CONTRACT_KEYS = ["labels", "matrix", "tn", "fp", "fn", "tp", "recall", "total", "failed", "model_version",
                 "limitations"]


class MetricsTestCase(InspectionTestCase):
    def setUp(self):
        super().setUp()
        self.report = self.media / "metrics" / "inspection_metrics.json"
        self.enterContext(override_settings(INSPECTION_METRICS_FILE=self.report))

    def evaluate(self, samples_dir: Path = SAMPLES) -> str:
        out = StringIO()
        call_command("evaluate_samples", samples_dir=samples_dir, stdout=out)
        return out.getvalue()

    def make_samples(self, rows: list[tuple[str, str, Path | None]]) -> Path:
        """A samples folder: (image_id, label, source image or None for a missing file)."""
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "images").mkdir()
        with (folder / "labels.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["image_id", "relative_path", "product_id", "label"])
            for image_id, label, source in rows:
                relative = f"images/{image_id}{source.suffix if source else '.png'}"
                if source:
                    shutil.copy(source, folder / relative)
                writer.writerow([image_id, relative, "P-PLATE01", label])
        return folder

    def metrics(self):
        self.as_role("ENGINEER")
        return self.client.get("/api/inspections/metrics/")


class OfficialSamplesTests(MetricsTestCase):
    def test_official_60_samples_through_the_upload_path(self):
        with mock.patch("backend.inspections.evaluation.validate_image", wraps=validation.validate_image) as check:
            output = self.evaluate()
        self.assertEqual(check.call_count, 60)  # every sample goes through upload validation
        self.assertIn("TN=30  FP=0  FN=0  TP=30", output)
        response = self.metrics()
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertEqual(list(data), CONTRACT_KEYS)
        self.assertEqual(data["labels"], ["Normal", "Defective"])
        self.assertEqual(data["matrix"], [[30, 0], [0, 30]])
        self.assertEqual((data["tn"], data["fp"], data["fn"], data["tp"]), (30, 0, 0, 30))
        self.assertEqual((data["recall"], data["total"], data["failed"]), (1.0, 60, []))
        self.assertEqual(data["model_version"], "BIT-PLATE-CV-1.0")
        self.assertEqual(data["limitations"], LIMITATIONS)

    def test_evaluation_writes_no_database_rows(self):
        self.evaluate()
        self.assertFalse(Inspection.objects.exists() or InspectionBatch.objects.exists()
                         or InspectionReview.objects.exists())


class ScoringTests(MetricsTestCase):
    def fake_model(self, predictions: dict[str, str]):
        """Patch the adapter so each sample's prediction is known; the model input stays the image path only."""
        def predict(path):
            self.assertIsInstance(path, str)
            return {"predicted_class": predictions[Path(path).stem], "confidence": 0.9, "defect_probability": 0.5,
                    "model_version": "BIT-PLATE-CV-1.0"}
        return mock.patch("backend.inspections.ai_adapter.predict_image", side_effect=predict)

    def test_matrix_rows_are_actual_and_columns_predicted(self):
        image = SAMPLES / "images" / "inspection_001.png"
        rows = [("N1", "Normal", image), ("N2", "Normal", image), ("N3", "Normal", image),
                ("D1", "Defective", image), ("D2", "Defective", image)]
        folder = self.make_samples(rows)
        # 2 false positives (Normal -> Defective), 1 false negative (Defective -> Normal)
        with self.fake_model({"N1": "Defective", "N2": "Defective", "N3": "Normal", "D1": "Normal", "D2": "Defective"}):
            self.evaluate(folder)
        data = self.metrics().data
        self.assertEqual(data["matrix"], [[1, 2], [1, 1]])  # [[TN, FP], [FN, TP]]
        self.assertEqual((data["tn"], data["fp"], data["fn"], data["tp"]), (1, 2, 1, 1))
        self.assertEqual(data["recall"], 0.5)  # TP / (TP + FN) = 1 / 2

    def test_recall_is_null_without_defective_samples(self):
        image = SAMPLES / "images" / "inspection_002.jpg"
        folder = self.make_samples([("N1", "Normal", image), ("N2", "Normal", image)])
        with self.fake_model({"N1": "Normal", "N2": "Defective"}):
            self.evaluate(folder)
        data = self.metrics().data
        self.assertIsNone(data["recall"])
        self.assertEqual(data["matrix"], [[1, 1], [0, 0]])

    def test_missing_invalid_and_model_failures_are_reported_never_dropped(self):
        image = SAMPLES / "images" / "inspection_001.png"
        folder = self.make_samples([("OK", "Defective", image), ("GONE", "Normal", None),
                                    ("SMALL", "Normal", PACK / "Validation" / "too_small.png"),
                                    ("TEXT", "Normal", PACK / "Validation" / "not_an_image.jpg"),
                                    ("BAD", "Sideways", image)])
        with self.assertRaises(CommandError) as raised:
            self.evaluate(folder)
        self.assertIn("4 of 5 samples have no prediction", str(raised.exception))
        data = self.metrics().data  # the report is still written
        self.assertEqual(data["total"], 5)
        self.assertEqual((data["tp"], data["fn"]), (1, 0))  # inspection_001 is a Defective sample the model detects
        self.assertEqual(data["failed"], [
            {"image_id": "GONE", "file": "images/GONE.png", "reason": "file missing"},
            {"image_id": "SMALL", "file": "images/SMALL.png",
             "reason": "The image is 32×32 px; each side must be 64–4096 px."},
            {"image_id": "TEXT", "file": "images/TEXT.jpg", "reason": "The file content is not a JPEG or PNG image."},
            {"image_id": "BAD", "file": "images/BAD.png", "reason": "invalid label 'Sideways'"},
        ])

    def test_model_error_is_a_failed_sample(self):
        folder = self.make_samples([("D1", "Defective", SAMPLES / "images" / "inspection_001.png")])
        with mock.patch("inspection_ai.predict_image", side_effect=RuntimeError("boom")):
            with self.assertRaises(CommandError):
                self.evaluate(folder)
        data = self.metrics().data
        self.assertEqual(data["failed"], [{"image_id": "D1", "file": "images/D1.png",
                                           "reason": "The model could not process this image."}])
        self.assertIsNone(data["recall"])
        self.assertEqual(data["total"], 1)

    def test_unreadable_csv_and_failed_integrity_write_nothing(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (folder / "labels.csv").write_text("id,label\n1,Normal\n")
        with self.assertRaisesMessage(CommandError, "missing CSV columns"):
            self.evaluate(folder)
        with self.assertRaisesMessage(CommandError, "Cannot read the labeled samples"):
            self.evaluate(folder / "nope")
        with mock.patch("backend.inspections.management.commands.evaluate_samples.verify_model_integrity",
                        return_value=False):
            with self.assertRaisesMessage(CommandError, "SHA256SUMS"):
                self.evaluate()
        self.assertFalse(self.report.exists())


class MetricsApiTests(MetricsTestCase):
    def test_404_until_generated(self):
        response = self.metrics()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.data, {"detail": "Metrics have not been generated yet."})

    def test_every_role_can_read_anonymous_cannot_and_it_is_read_only(self):
        self.report.parent.mkdir(parents=True)
        stored = {"labels": ["Normal", "Defective"], "matrix": [[1, 0], [0, 1]], "tn": 1, "fp": 0, "fn": 0, "tp": 1,
                  "recall": 1.0, "total": 2, "failed": [], "model_version": "BIT-PLATE-CV-1.0"}
        self.report.write_text(json.dumps(stored))
        self.assertEqual(self.client.get("/api/inspections/metrics/").status_code, 401)
        for role in ("ENGINEER", "SUPERVISOR", "ADMIN"):
            with self.subTest(role):
                self.as_role(role)
                self.assertEqual(self.client.get("/api/inspections/metrics/").status_code, 200)
                self.assertEqual(self.client.post("/api/inspections/metrics/", {}).status_code, 405)
