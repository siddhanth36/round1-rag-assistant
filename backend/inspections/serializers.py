from rest_framework import serializers

from backend.inspections.models import Inspection, InspectionReview, Product


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


class InspectionSerializer(serializers.ModelSerializer):
    product = serializers.CharField(source="product.product_id", read_only=True)
    submitted_by = serializers.CharField(source="submitted_by.username", read_only=True)
    image_url = serializers.SerializerMethodField()
    original = serializers.SerializerMethodField()
    reviews = ReviewSerializer(many=True, read_only=True)

    class Meta:
        model = Inspection
        fields = ["id", "product", "batch", "original_filename", "image_url", "image_format", "width", "height",
                  "status", "failure_reason", "original", "review_status", "reviews",
                  "submitted_by", "created_at", "started_at", "completed_at"]

    def get_image_url(self, inspection: Inspection) -> str | None:
        return f"/api/inspections/{inspection.id}/image/" if inspection.image else None

    def get_original(self, inspection: Inspection) -> dict | None:
        """The immutable model output; null until the inspection completed."""
        if inspection.status != Inspection.Status.COMPLETED:
            return None
        return {"predicted_class": inspection.predicted_class, "confidence": inspection.confidence,
                "defect_probability": inspection.defect_probability, "model_version": inspection.model_version}

    def to_representation(self, inspection: Inspection) -> dict:
        data = super().to_representation(inspection)
        data["current_review"] = data["reviews"][0] if data["reviews"] else None  # reviews are newest first
        return data

