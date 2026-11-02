from datetime import date, timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import generics, serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from backend.accounts.models import User
from backend.accounts.permissions import HasRole, IsEngineer
from backend.assistant.models import Answer, Question
from backend.machines.models import Machine
from backend.machines.serializers import MachineSerializer

IsSupervisorOrAdmin = HasRole(User.Role.SUPERVISOR, User.Role.ADMIN)
UNANSWERED = "unanswered"  # filter value: insufficient_evidence, error, or no Answer row


class QuestionSerializer(serializers.BaseSerializer):
    """Read-only. ``retrieval_trace`` and ``asked_by`` are added only for staff (context["staff"])."""

    def to_representation(self, q: Question) -> dict:
        answer = getattr(q, "answer", None)  # missing reverse one-to-one -> None
        data = {
            "id": q.id,
            "machine": MachineSerializer(q.machine).data,
            "question": q.text,
            "created_at": serializers.DateTimeField().to_representation(q.created_at),
            "status": answer.status if answer else None,
            "answer": answer.text if answer else None,
            "latency_ms": answer.latency_ms if answer else None,
            "citations": [
                {"source_tag": c.source_tag, "manual_id": c.manual_id, "document_id": c.document_id,
                 "revision": c.revision, "pdf_page": c.pdf_page, "passage": c.passage}
                for c in (answer.citations.all() if answer else [])
            ],
        }
        if self.context.get("staff"):
            data |= {"asked_by": q.user.username, "retrieval_trace": answer.retrieval_trace if answer else None}
        return data


class Page(PageNumberPagination):
    page_size = 25


def questions():
    return (Question.objects.select_related("machine", "user", "answer")
            .prefetch_related("answer__citations").order_by("-created_at", "-id"))


def unanswered(qs):
    return qs.exclude(answer__status=Answer.Status.ANSWERED)  # also keeps questions with no Answer row


def filter_questions(qs, params):
    """Shared ?status= and ?machine= filters (history + supervisor log)."""
    if status := params.get("status"):
        if status == UNANSWERED:
            qs = unanswered(qs)
        elif status in Answer.Status.values:
            qs = qs.filter(answer__status=status)
        else:
            raise ValidationError({"status": f"Use one of: {', '.join(Answer.Status.values)}, {UNANSWERED}."})
    if machine := params.get("machine"):
        qs = qs.filter(machine__machine_id=machine)
    return qs


class History(generics.ListAPIView):
    """The engineer's own questions only: the filter is the logged-in user, never a parameter."""

    permission_classes = [IsEngineer]
    serializer_class = QuestionSerializer
    pagination_class = Page

    def get_queryset(self):
        return filter_questions(questions().filter(user=self.request.user), self.request.query_params)


class SupervisorQuestions(generics.ListAPIView):
    permission_classes = [IsSupervisorOrAdmin]
    serializer_class = QuestionSerializer
    pagination_class = Page

    def get_serializer_context(self):
        return super().get_serializer_context() | {"staff": True}

    def get_queryset(self):
        params = self.request.query_params
        qs = filter_questions(questions(), params)
        if user := params.get("user"):
            qs = qs.filter(user__username=user)
        for param, lookup in (("from", "created_at__date__gte"), ("to", "created_at__date__lte")):
            if value := params.get(param):
                try:
                    qs = qs.filter(**{lookup: date.fromisoformat(value)})
                except ValueError:
                    raise ValidationError({param: "Use a date in YYYY-MM-DD format."}) from None
        return qs


@api_view(["GET"])
@permission_classes([IsSupervisorOrAdmin])
def analytics(request):
    by_status = dict(Answer.objects.values_list("status").annotate(n=Count("id")).order_by())
    counts = {status: by_status.get(status, 0) for status in Answer.Status.values}
    total = Question.objects.count()

    per_machine = Machine.objects.order_by("machine_id").annotate(
        total=Count("questions"),
        **{status: Count("questions", filter=Q(questions__answer__status=status)) for status in Answer.Status.values},
    ).values("machine_id", "model", "total", *Answer.Status.values)

    today = timezone.localdate()
    start = today - timedelta(days=6)
    daily = dict(
        Question.objects.filter(created_at__date__gte=start).annotate(day=TruncDate("created_at"))
        .values_list("day").annotate(n=Count("id")).order_by()
    )

    recent = unanswered(questions())[:10]
    return Response({
        "total": total,
        "by_status": counts,
        "unanswered": total - counts[Answer.Status.ANSWERED],
        "per_machine": list(per_machine),
        "per_day": [{"date": (start + timedelta(days=i)).isoformat(), "count": daily.get(start + timedelta(days=i), 0)}
                    for i in range(7)],
        "recent_unanswered": [
            {"id": q.id, "machine_id": q.machine.machine_id, "question": q.text, "asked_by": q.user.username,
             "created_at": serializers.DateTimeField().to_representation(q.created_at),
             "status": getattr(getattr(q, "answer", None), "status", None)}
            for q in recent
        ],
    })
