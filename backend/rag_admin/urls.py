from django.urls import path

from rag_admin.api.views import DocumentDeleteAPIView, DocumentListCreateAPIView, RagSettingsAPIView


urlpatterns = [
    path("settings/", RagSettingsAPIView.as_view(), name="rag-settings"),
    path("documents/", DocumentListCreateAPIView.as_view(), name="rag-documents"),
    path("documents/<int:id>/", DocumentDeleteAPIView.as_view(), name="rag-document-delete"),
]
