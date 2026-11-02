from django.contrib import admin

from backend.assistant.models import Answer, Citation, Question


class ReadOnlyAdmin(admin.ModelAdmin):
    """The question log is immutable through the application, admin included."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Question)
class QuestionAdmin(ReadOnlyAdmin):
    list_display = ("id", "user", "machine", "created_at", "text")


@admin.register(Answer)
class AnswerAdmin(ReadOnlyAdmin):
    list_display = ("question", "status", "latency_ms")


@admin.register(Citation)
class CitationAdmin(ReadOnlyAdmin):
    list_display = ("answer", "source_tag", "document_id", "revision", "pdf_page")
