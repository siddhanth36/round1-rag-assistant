from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.clickjacking import xframe_options_sameorigin
from rest_framework import generics, status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from backend.accounts.permissions import IsAdmin
from backend.machines.models import Machine, Manual
from backend.machines.serializers import MachineSerializer, ManualSerializer, ManualUploadSerializer


class MachineList(generics.ListCreateAPIView):
    """GET: any logged-in user. POST: Admin only."""

    queryset = Machine.objects.order_by("machine_id")
    serializer_class = MachineSerializer

    def get_permissions(self):
        return [IsAdmin()] if self.request.method == "POST" else [IsAuthenticated()]


class ManualList(generics.ListAPIView):
    serializer_class = ManualSerializer

    def get_queryset(self):
        manuals = Manual.objects.select_related("machine", "uploaded_by").order_by("machine__machine_id", "document_id", "revision")
        machine = self.request.query_params.get("machine")
        return manuals.filter(machine__machine_id=machine) if machine else manuals


class ManualDetail(generics.RetrieveAPIView):
    queryset = Manual.objects.select_related("machine", "uploaded_by")
    serializer_class = ManualSerializer


@api_view(["POST"])
@permission_classes([IsAdmin])  # checked before the request body (the file) is parsed
@parser_classes([MultiPartParser])
def upload_manual(request):
    serializer = ManualUploadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    manual = serializer.save(uploaded_by=request.user)
    return Response(ManualSerializer(manual).data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def manual_page(request, pk: int, page: int):
    manual = get_object_or_404(Manual, pk=pk)
    if not 1 <= page <= manual.page_count:
        raise Http404
    return Response({"document_id": manual.document_id, "revision": manual.revision,
                     "page": page, "page_count": manual.page_count,
                     "file_url": f"/api/manuals/{manual.id}/file/#page={page}"})


@xframe_options_sameorigin  # the same-origin viewer page embeds the PDF; everything else stays DENY
@api_view(["GET"])
def manual_file(request, pk: int):
    """Authenticated PDF stream. MEDIA_ROOT has no public URL, so this is the only way to read a manual."""
    manual = get_object_or_404(Manual, pk=pk)
    return FileResponse(manual.file.open("rb"), content_type="application/pdf", filename=manual.original_filename)
