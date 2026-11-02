"""validate_image() rules from the brief, plus the database guards on Inspection and InspectionReview."""

import zlib
from io import BytesIO
from unittest import mock

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase
from PIL import Image

from backend.accounts.models import User
from backend.inspections.models import Inspection, InspectionReview, Product
from backend.inspections.validation import MAX_BYTES, ImageRejected, validate_image

PACK = settings.BASE_DIR / "challenge-pack-r2"


def image_bytes(width: int = 64, height: int = 64, fmt: str = "PNG") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), (90, 90, 90)).save(buffer, fmt)
    return buffer.getvalue()


def upload(content: bytes, name: str = "upload.png") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


class ValidationFileTests(SimpleTestCase):
    """The three intentional invalid inputs shipped in the Round 2 pack."""

    def reason(self, name: str) -> str:
        with self.assertRaises(ImageRejected) as ctx:
            validate_image(upload((PACK / "Validation" / name).read_bytes(), name))
        return str(ctx.exception)

    def test_text_with_jpg_extension_is_not_an_image(self):
        self.assertEqual(self.reason("not_an_image.jpg"), "The file content is not a JPEG or PNG image.")

    def test_truncated_png_is_corrupt(self):
        self.assertEqual(self.reason("corrupt_image.png"), "The image data is corrupt or truncated.")

    def test_32px_png_is_below_minimum(self):
        self.assertEqual(self.reason("too_small.png"), "The image is 32×32 px; each side must be 64–4096 px.")


class ValidationRuleTests(SimpleTestCase):
    def assertRejected(self, content: bytes, fragment: str, name: str = "x.png"):
        with self.assertRaises(ImageRejected) as ctx:
            validate_image(upload(content, name))
        self.assertIn(fragment, str(ctx.exception))

    def test_every_sample_image_is_accepted(self):
        for path in sorted((PACK / "Samples" / "images").iterdir()):
            with self.subTest(path.name):
                result = validate_image(upload(path.read_bytes(), path.name))
                self.assertEqual((result.width, result.height), (256, 256))
                self.assertEqual(result.format, "PNG" if path.suffix == ".png" else "JPEG")

    def test_empty_file(self):
        self.assertRejected(b"", "empty")

    def test_over_5_mib_is_rejected_before_pillow_reads_it(self):
        with mock.patch("backend.inspections.validation.Image.open") as pillow:
            self.assertRejected(b"\x89PNG\r\n\x1a\n" + b"\0" * MAX_BYTES, "limit is 5 MiB")
        pillow.assert_not_called()

    def test_exactly_5_mib_passes_the_size_rule(self):
        content = image_bytes()
        result = validate_image(upload(content + b"\0" * (MAX_BYTES - len(content))))  # bytes after IEND
        self.assertEqual(result.size, MAX_BYTES)

    def test_dimension_boundaries_are_inclusive(self):
        for width, height in [(64, 64), (4096, 64), (64, 4096)]:
            with self.subTest(size=(width, height)):
                self.assertEqual(validate_image(upload(image_bytes(width, height))).width, width)
        for width, height in [(63, 64), (64, 63), (4097, 64), (64, 4097)]:
            with self.subTest(size=(width, height)):
                self.assertRejected(image_bytes(width, height), "each side must be 64–4096 px")

    def test_other_real_image_formats_are_rejected(self):
        for fmt in ["GIF", "BMP", "WEBP", "TIFF"]:
            with self.subTest(fmt):
                self.assertRejected(image_bytes(fmt=fmt), "not a JPEG or PNG")

    def test_format_comes_from_content_not_extension(self):
        self.assertEqual(validate_image(upload(image_bytes(fmt="PNG"), "photo.jpg")).format, "PNG")
        self.assertEqual(validate_image(upload(image_bytes(fmt="JPEG"), "scan.bin.png")).extension, ".jpg")

    def test_truncated_jpeg_is_corrupt(self):
        self.assertRejected(image_bytes(256, 256, "JPEG")[:400], "corrupt or truncated")

    def test_decompression_bomb_header_is_rejected(self):
        # 100000×100000 header with almost no data: rejected from the header, never decoded
        bomb = bytearray(image_bytes())
        bomb[16:24] = (100000).to_bytes(4, "big") * 2  # IHDR width/height
        bomb[29:33] = zlib.crc32(bytes(bomb[12:29])).to_bytes(4, "big")  # valid IHDR CRC
        self.assertRejected(bytes(bomb), "too many pixels")


class InspectionIntegrityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="engineer", password="x", role=User.Role.ENGINEER)
        self.product = Product.objects.create(product_id="P-PLATE01", product_name="Demonstration metal plate")

    def test_completed_without_prediction_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError):
            Inspection.objects.create(product=self.product, submitted_by=self.user, original_filename="a.png",
                                      status=Inspection.Status.COMPLETED)

    def test_failed_with_prediction_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError):
            Inspection.objects.create(product=self.product, submitted_by=self.user, original_filename="a.png",
                                      status=Inspection.Status.FAILED, predicted_class="Normal")

    def test_reviews_are_append_only(self):
        inspection = Inspection.objects.create(
            product=self.product, submitted_by=self.user, original_filename="a.png",
            status=Inspection.Status.COMPLETED, predicted_class="Normal", confidence=0.9,
            defect_probability=0.1, model_version="BIT-PLATE-CV-1.0")
        review = InspectionReview.objects.create(inspection=inspection, reviewer=self.user, reviewed_class="Normal",
                                                 decision=InspectionReview.Decision.CONFIRMED)
        review.comment = "changed"
        with self.assertRaises(ValueError):
            review.save()
