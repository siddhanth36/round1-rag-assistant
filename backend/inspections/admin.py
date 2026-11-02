from django.contrib import admin

from backend.inspections.models import Inspection, InspectionReview, Product


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    """Product administration (the brief allows Django admin for this)."""

    list_display = ("product_id", "product_name", "is_active", "created_at")

    def get_readonly_fields(self, request, obj=None):
        return ("product_id",) if obj else ()

    def has_delete_permission(self, request, obj=None):
        return False  # deactivate instead: inspections reference products (PROTECT)


class ViewOnlyAdmin(admin.ModelAdmin):
    """Inspections and reviews are created only through the API; the admin can never edit or delete them."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Inspection)
class InspectionAdmin(ViewOnlyAdmin):
    list_display = ("id", "product", "submitted_by", "status", "predicted_class", "confidence", "review_status",
                    "created_at")
    list_filter = ("status", "predicted_class", "review_status", "product")


@admin.register(InspectionReview)
class InspectionReviewAdmin(ViewOnlyAdmin):
    list_display = ("id", "inspection", "reviewer", "decision", "reviewed_class", "created_at")
