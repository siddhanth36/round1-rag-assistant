import uuid
from pathlib import PurePath

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q


class Product(models.Model):
    """A supported product (P-PLATE01). ``product_id`` never changes after creation."""

    product_id = models.CharField(max_length=32, unique=True, validators=[RegexValidator(r"^[A-Z0-9-]+$")])
    product_name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.product_id


class InspectionBatch(models.Model):
    """A 1-10 file upload. Its status is derived from its items, never stored."""

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="batches")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


def inspection_upload_path(instance, filename: str) -> str:
    """Server-generated name; the extension comes from the validated format, never from the client."""
    return f"inspections/{uuid.uuid4().hex}{PurePath(filename).suffix}"


class Inspection(models.Model):
    """One image inspection. The original prediction fields are written once, by services.record_prediction."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        PROCESSING = "processing"
        COMPLETED = "completed"
        FAILED = "failed"

    class Label(models.TextChoices):
        NORMAL = "Normal"
        DEFECTIVE = "Defective"

    class ReviewStatus(models.TextChoices):
        UNREVIEWED = "unreviewed"
        CONFIRMED = "confirmed"
        CORRECTED = "corrected"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="inspections")
    batch = models.ForeignKey(InspectionBatch, null=True, blank=True, on_delete=models.PROTECT, related_name="items")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="inspections")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    failure_reason = models.TextField(blank=True)

    original_filename = models.CharField(max_length=255)  # display only; never used as a path
    image = models.FileField(upload_to=inspection_upload_path, blank=True)  # empty for rejected uploads
    image_format = models.CharField(max_length=4, blank=True)  # decoded format: JPEG / PNG
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    file_size = models.PositiveIntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)

    # Original model output: immutable once status is completed.
    predicted_class = models.CharField(max_length=10, choices=Label.choices, null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    defect_probability = models.FloatField(null=True, blank=True)
    model_version = models.CharField(max_length=64, null=True, blank=True)

    # Filter copy of the latest review; the review history itself lives in InspectionReview.
    review_status = models.CharField(max_length=10, choices=ReviewStatus.choices, default=ReviewStatus.UNREVIEWED)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["submitted_by", "-created_at"], name="insp_user_created"),
            models.Index(fields=["product", "-created_at"], name="insp_product_created"),
            models.Index(fields=["status"], name="insp_status"),
            models.Index(fields=["predicted_class"], name="insp_predicted"),
            models.Index(fields=["review_status"], name="insp_review_status"),
        ]
        constraints = [  # CHECK constraints: PostgreSQL and MySQL 8.0.16+
            models.CheckConstraint(
                condition=~Q(status="completed") | Q(predicted_class__isnull=False, confidence__isnull=False,
                                                      defect_probability__isnull=False, model_version__isnull=False),
                name="insp_completed_has_prediction"),
            models.CheckConstraint(condition=Q(status="completed") | Q(predicted_class__isnull=True),
                                   name="insp_prediction_only_when_completed"),
            models.CheckConstraint(condition=Q(confidence__isnull=True) | Q(confidence__gte=0, confidence__lte=1),
                                   name="insp_confidence_range"),
            models.CheckConstraint(
                condition=Q(defect_probability__isnull=True) | Q(defect_probability__gte=0, defect_probability__lte=1),
                name="insp_defect_probability_range"),
        ]


class InspectionReview(models.Model):
    """Append-only supervisor review. A new review adds a row; the latest one is current."""

    class Decision(models.TextChoices):
        CONFIRMED = "confirmed"
        CORRECTED = "corrected"

    inspection = models.ForeignKey(Inspection, on_delete=models.PROTECT, related_name="reviews")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    reviewed_class = models.CharField(max_length=10, choices=Inspection.Label.choices)
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comment = models.TextField(blank=True, max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]  # newest first: reviews[0] is the current review

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("Inspection reviews are append-only.")
        super().save(*args, **kwargs)
