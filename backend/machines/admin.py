from django.contrib import admin

from backend.machines.models import Machine, Manual


@admin.register(Machine)
class MachineAdmin(admin.ModelAdmin):
    list_display = ("machine_id", "model", "machine_name")

    def get_readonly_fields(self, request, obj=None):
        return ("machine_id",) if obj else ()  # machine_id is stored in every vector record: immutable


@admin.register(Manual)
class ManualAdmin(admin.ModelAdmin):
    """View/delete only. New manuals go through POST /api/manuals/upload/ so they are always validated."""

    list_display = ("document_id", "revision", "machine", "page_count", "indexing_status", "uploaded_by", "uploaded_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
