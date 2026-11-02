from rest_framework import serializers

from backend.inspections import services
from backend.inspections.models import Inspection, InspectionBatch, InspectionReview, Product


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["product_id", "product_name", "description", "is_active"]

    def get_fields(self):
        fields = super().get_fields()
        if self.instance is not None:  # PATCH: product_id is immutable once created
            fields["product_id"].read_only = True
        return fields


class ReviewSerializer(serializers.ModelSerializer):
    reviewer = serializers.CharField(source="reviewer.username", read_only=True)

    class Meta:
        model = InspectionReview
        fields = ["id", "reviewed_class", "decision", "comment", "reviewer", "created_at"]


class PredictionFieldsMixin(serializers.Serializer):
    """``image_url`` (protected endpoint, null for rejected uploads) and ``original`` (immutable model output)."""

    image_url = serializers.SerializerMethodField()
    original = serializers.SerializerMethodField()

    def get_image_url(self, inspection: Inspection) -> str | None:
        return f"/api/inspections/{inspection.id}/image/" if inspection.image else None

    def get_original(self, inspection: Inspection) -> dict | None:
        """The immutable model output; null until the inspection completed."""
        if inspection.status != Inspection.Status.COMPLETED:
            return None
        return {"predicted_class": inspection.predicted_class, "confidence": inspection.confidence,
                "defect_probability": inspection.defect_probability, "model_version": inspection.model_version}


class InspectionSummarySerializer(PredictionFieldsMixin, serializers.ModelSerializer):
    """History row: the inspection object without ``reviews`` (contract §3.5)."""

    product = serializers.CharField(source="product.product_id", read_only=True)
    submitted_by = serializers.CharField(source="submitted_by.username", read_only=True)
    current_review = serializers.SerializerMethodField()

    class Meta:
        model = Inspection
        fields = ["id", "product", "batch", "original_filename", "image_url", "image_format", "width", "height",
                  "status", "failure_reason", "original", "review_status", "current_review",
                  "submitted_by", "created_at", "started_at", "completed_at"]

    def get_current_review(self, inspection: Inspection) -> dict | None:
        reviews = inspection.reviews.all()  # newest first (model ordering); prefetched by the views
        return ReviewSerializer(reviews[0]).data if reviews else None


class InspectionSerializer(InspectionSummarySerializer):
    """The full inspection object (detail, single upload, review response): ``reviews`` newest first."""

    reviews = ReviewSerializer(many=True, read_only=True)

    class Meta(InspectionSummarySerializer.Meta):
        fields = ["id", "product", "batch", "original_filename", "image_url", "image_format", "width", "height",
                  "status", "failure_reason", "original", "review_status", "current_review", "reviews",
                  "submitted_by", "created_at", "started_at", "completed_at"]


class BatchItemSerializer(PredictionFieldsMixin, serializers.ModelSerializer):
    class Meta:
        model = Inspection
        fields = ["id", "original_filename", "status", "failure_reason", "original", "image_url",
                  "started_at", "completed_at"]


class BatchSummarySerializer(serializers.ModelSerializer):
    """Batch list row. Expects a batch from ``services.batches_with_counts()``."""

    batch_id = serializers.IntegerField(source="id", read_only=True)
    product = serializers.CharField(source="product.product_id", read_only=True)
    submitted_by = serializers.CharField(source="submitted_by.username", read_only=True)
    counts = serializers.SerializerMethodField()
    done = serializers.SerializerMethodField()

    class Meta:
        model = InspectionBatch
        fields = ["batch_id", "product", "submitted_by", "created_at", "counts", "done"]

    def get_counts(self, batch) -> dict:
        return services.batch_counts(batch)

    def get_done(self, batch) -> bool:
        return services.batch_done(services.batch_counts(batch))


class ReviewInputSerializer(serializers.Serializer):
    """Review request body. ``predicted_class`` (context) is the inspection's original prediction."""

    decision = serializers.ChoiceField(choices=InspectionReview.Decision.choices)
    reviewed_class = serializers.ChoiceField(choices=Inspection.Label.choices)
    comment = serializers.CharField(max_length=1000, allow_blank=True, required=False, default="")

    def validate(self, data):
        predicted = self.context["predicted_class"]
        if data["decision"] == InspectionReview.Decision.CONFIRMED and data["reviewed_class"] != predicted:
            raise serializers.ValidationError(
                {"reviewed_class": [f"A confirmation must keep the predicted class ({predicted})."]})
        if data["decision"] == InspectionReview.Decision.CORRECTED and data["reviewed_class"] == predicted:
            raise serializers.ValidationError(
                {"reviewed_class": [f"A correction must change the predicted class ({predicted})."]})
        return data
