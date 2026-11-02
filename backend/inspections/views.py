from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from backend.accounts.models import User
from backend.accounts.permissions import HasRole, IsAdmin
from backend.inspections import services
from backend.inspections.models import Inspection, Product
from backend.inspections.serializers import InspectionSerializer, ProductSerializer

CanSubmit = HasRole(User.Role.ENGINEER, User.Role.ADMIN)  # Admin too, for the demo; Supervisor never submits


def visible_inspections(user):
    """Engineers see only their own inspections; Supervisors and Admins see all."""
    inspections = Inspection.objects.select_related("product", "submitted_by")
    return inspections.filter(submitted_by=user) if user.role == User.Role.ENGINEER else inspections



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


@api_view(["POST"])
@permission_classes([CanSubmit])  # checked before the multipart body is parsed
@parser_classes([MultiPartParser])
def create_inspection(request):
    """Single image inspection, run synchronously. ``submitted_by`` is always the session user."""
    product = Product.objects.filter(product_id=request.data.get("product_id", ""), is_active=True).first()
    if product is None:
        raise ValidationError({"product_id": ["Select an active product."]})
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
    return FileResponse(inspection.image.open("rb"), content_type=f"image/{inspection.image_format.lower()}")

