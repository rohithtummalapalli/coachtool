from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from rag_admin.models import Document
from rag_admin.serializers import DocumentSerializer, DocumentUploadSerializer, RagSettingsSerializer
from rag_admin.services.document_service import handle_document_upload
from rag_admin.services.settings_service import get_active_settings, update_settings


class RagSettingsAPIView(APIView):
    def get_permissions(self):
        if self.request.method == "GET":
            permission_classes = [IsAuthenticated]
        else:
            permission_classes = [IsAdminUser]
        return [permission() for permission in permission_classes]

    def get(self, request):
        settings_obj = get_active_settings()
        serializer = RagSettingsSerializer(settings_obj)
        return Response(serializer.data)

    def put(self, request):
        active = get_active_settings()
        serializer = RagSettingsSerializer(active, data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        try:
            updated = update_settings(serializer.validated_data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(RagSettingsSerializer(updated).data)


class DocumentListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = Document.objects.select_related("created_by").all().order_by("-created_at")
        serializer = DocumentSerializer(queryset, many=True)
        return Response(serializer.data)

    def post(self, request):
        upload_serializer = DocumentUploadSerializer(data=request.data)
        upload_serializer.is_valid(raise_exception=True)
        document = handle_document_upload(upload_serializer.validated_data["file"], request.user)
        return Response(DocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class DocumentDeleteAPIView(APIView):
    permission_classes = [IsAdminUser]

    def delete(self, request, id: int):
        document = get_object_or_404(Document, pk=id)
        document.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
