import logging

from django import forms
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.utils.urls import remove_query_param, replace_query_param
from rest_framework.views import APIView

from backend.accounts.models import User
from backend.accounts.permissions import HasRole, IsAdmin, IsSupervisor
from backend.inspections import evaluation, services
from backend.inspections.models import Inspection, Product
from backend.inspections.serializers import (BatchItemSerializer, BatchSummarySerializer, InspectionSerializer,
                                             InspectionSummarySerializer, ProductSerializer, ReviewInputSerializer)


logger = logging.getLogger(__name__)

CanSubmit = HasRole(User.Role.ENGINEER, User.Role.ADMIN)  # Admin too, for the demo; Supervisor never submits


def visible_inspections(user):
    """Engineers see only their own inspections; Supervisors and Admins see all."""
    inspections = (Inspection.objects.select_related("product", "submitted_by")
                   .prefetch_related("reviews__reviewer"))
    return inspections.filter(submitted_by=user) if user.role == User.Role.ENGINEER else inspections


def visible_batches(user):
    batches = services.batches_with_counts()
    return batches.filter(submitted_by=user) if user.role == User.Role.ENGINEER else batches


def active_product(product_id) -> Product:
    product = Product.objects.filter(product_id=product_id or "", is_active=True).first()
    if product is None:
        raise ValidationError({"product_id": ["Select an active product."]})
    return product



class ProductList(generics.ListCreateAPIView):
    """GET: any logged-in user, active products. POST: Admin only."""

    queryset = Product.objects.filter(is_active=True).order_by("product_id")
    serializer_class = ProductSerializer

    def get_permissions(self):
        return [IsAdmin()] if self.request.method == "POST" else [IsAuthenticated()]


class ProductDetail(generics.UpdateAPIView):
    """PATCH /api/products/<product_id>/: Admin only (name, description, is_active)."""

    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsAdmin]
    lookup_field = "product_id"
    http_method_names = ["patch"]


class HistoryFilters(forms.Form):
    """Query parameters of GET /api/inspections/. Every value is validated; nothing is silently ignored."""

    prediction = forms.ChoiceField(choices=Inspection.Label.choices, required=False)
    review_status = forms.ChoiceField(choices=Inspection.ReviewStatus.choices, required=False)
    status = forms.ChoiceField(choices=Inspection.Status.choices, required=False)
    product = forms.ModelChoiceField(queryset=Product.objects.all(), to_field_name="product_id", required=False)
    to = forms.DateField(input_formats=["%Y-%m-%d"], required=False)
    page = forms.IntegerField(min_value=1, required=False)

    def clean(self):
        data = super().clean()
        if data.get("from") and data.get("to") and data["from"] > data["to"]:
            self.add_error("to", "The end date is before the start date.")
        return data


HistoryFilters.base_fields["from"] = forms.DateField(input_formats=["%Y-%m-%d"], required=False)  # a keyword


class HistoryPage(PageNumberPagination):
    """25 per page; next/previous are relative links, as in the contract (``/api/inspections/?page=2``)."""

    page_size = 25

    def get_next_link(self):
        if not self.page.has_next():
            return None
        return replace_query_param(self.request.get_full_path(), self.page_query_param, self.page.next_page_number())

    def get_previous_link(self):
        if not self.page.has_previous():
            return None
        url, number = self.request.get_full_path(), self.page.previous_page_number()
        return remove_query_param(url, self.page_query_param) if number == 1 else replace_query_param(
            url, self.page_query_param, number)


class Inspections(APIView):
    """GET: history (any role; Engineers see their own). POST: single synchronous inspection (Engineer, Admin)."""

    parser_classes = [MultiPartParser]  # POST body; parsed only after the permission check

    def get_permissions(self):
        return [CanSubmit()] if self.request.method == "POST" else [IsAuthenticated()]

    def get(self, request):
        unknown = sorted(set(request.query_params) - set(HistoryFilters.base_fields))
        if unknown:  # a misspelt filter must not silently return everything
            raise ValidationError({name: ["Unknown filter."] for name in unknown})
        filters = HistoryFilters(request.query_params)
        if not filters.is_valid():
            raise ValidationError(filters.errors)
        f = filters.cleaned_data
        lookups = {"predicted_class": f["prediction"], "review_status": f["review_status"], "status": f["status"],
                   "product": f["product"], "created_at__date__gte": f["from"], "created_at__date__lte": f["to"]}
        inspections = (visible_inspections(request.user)
                       .filter(**{key: value for key, value in lookups.items() if value not in (None, "")})
                       .order_by("-created_at", "-id"))
        paginator = HistoryPage()
        page = paginator.paginate_queryset(inspections, request, view=self)
        return paginator.get_paginated_response(InspectionSummarySerializer(page, many=True).data)

    def post(self, request):
        """Single image inspection, run synchronously. ``submitted_by`` is always the session user."""
        product = active_product(request.data.get("product_id"))
        upload = request.FILES.get("image")
        if upload is None:
            raise ValidationError({"image": ["No image file was submitted."]})

        inspection = services.create_inspection(upload, product=product, user=request.user)
        if inspection.status == Inspection.Status.FAILED:  # rejected upload: saved as a failed attempt
            return Response({"id": inspection.id, "status": inspection.status,
                             "failure_reason": inspection.failure_reason}, status=status.HTTP_400_BAD_REQUEST)

        inspection = services.record_prediction(inspection.id)
        data = InspectionSerializer(inspection).data
        if inspection.status == Inspection.Status.FAILED:  # InferenceError: never a fake result
            return Response(data, status=status.HTTP_502_BAD_GATEWAY)
        return Response(data, status=status.HTTP_201_CREATED)


class InspectionDetail(generics.RetrieveAPIView):
    serializer_class = InspectionSerializer

    def get_queryset(self):
        return visible_inspections(self.request.user)


@api_view(["GET"])
def inspection_image(request, pk: int):
    """Authenticated image stream with the same visibility as the detail. MEDIA_ROOT has no public URL."""
    inspection = get_object_or_404(visible_inspections(request.user), pk=pk)
    if not inspection.image:
        raise Http404
    try:
        stream = inspection.image.open("rb")
    except FileNotFoundError:
        logger.error("Inspection %s: stored image %r is missing", pk, inspection.image.name)
        raise Http404 from None
    return FileResponse(stream, content_type=f"image/{inspection.image_format.lower()}")


@api_view(["POST"])
@permission_classes([IsSupervisor])  # Engineer and Admin get 403 before the body is read
def review_inspection(request, pk: int):
    """Append a supervisor review. The original prediction never changes; reviews are never edited or deleted."""
    inspection = get_object_or_404(Inspection, pk=pk)  # Supervisors see every inspection
    if inspection.status != Inspection.Status.COMPLETED:
        return Response({"detail": "Only completed inspections can be reviewed."}, status=status.HTTP_409_CONFLICT)
    serializer = ReviewInputSerializer(data=request.data, context={"predicted_class": inspection.predicted_class})
    serializer.is_valid(raise_exception=True)
    try:
        services.add_review(pk, reviewer=request.user, **serializer.validated_data)
    except services.NotReviewable:  # re-checked under the row lock
        return Response({"detail": "Only completed inspections can be reviewed."}, status=status.HTTP_409_CONFLICT)
    return Response(InspectionSerializer(visible_inspections(request.user).get(pk=pk)).data,
                    status=status.HTTP_201_CREATED)


# --- batches ---------------------------------------------------------------------------------------

def batch_detail_data(batch) -> dict:
    """Batch detail (contract §3.4). ``batch`` comes from ``services.batches_with_counts()``."""
    data = BatchSummarySerializer(batch).data
    online = None if data["done"] else services.worker_online()
    data["worker_online"] = online
    data["warning"] = (services.WORKER_OFFLINE_WARNING
                       if online is False and data["counts"][Inspection.Status.QUEUED] else None)
    items = batch.items.order_by("id")  # ids are assigned in upload order
    data["items"] = BatchItemSerializer(items, many=True).data
    return data


class Batches(APIView):
    """GET: batches visible to the user, newest first. POST: a 1-10 image batch (Engineer, Admin)."""

    parser_classes = [MultiPartParser]

    def get_permissions(self):
        return [CanSubmit()] if self.request.method == "POST" else [IsAuthenticated()]

    def get(self, request):
        batches = visible_batches(request.user).order_by("-created_at", "-id")
        return Response(BatchSummarySerializer(batches, many=True).data)

    def post(self, request):
        uploads = request.FILES.getlist("images")
        if not 1 <= len(uploads) <= services.MAX_BATCH_FILES:  # checked first: nothing is saved
            raise ValidationError({"images": [f"A batch must contain 1 to {services.MAX_BATCH_FILES} images; "
                                              f"{len(uploads)} received."]})
        product = active_product(request.data.get("product_id"))
        batch = services.create_batch(uploads, product=product, user=request.user)
        url = f"/api/inspections/batches/{batch.id}/"
        return Response(batch_detail_data(services.batches_with_counts().get(pk=batch.id)),
                        status=status.HTTP_202_ACCEPTED, headers={"Location": url})


@api_view(["GET"])
def batch_detail(request, pk: int):
    return Response(batch_detail_data(get_object_or_404(visible_batches(request.user), pk=pk)))


# --- metrics ---------------------------------------------------------------------------------------

@api_view(["GET"])
def metrics(request):
    """The report written by ``manage.py evaluate_samples``; read-only for every logged-in user."""
    try:
        return Response(evaluation.load_metrics())
    except evaluation.MetricsUnavailable:
        raise NotFound("Metrics have not been generated yet.") from None
