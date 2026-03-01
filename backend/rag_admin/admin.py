from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path

from rag_admin.models import Document, RagSettings
from rag_admin.services.document_service import handle_document_upload


class DocumentUploadForm(forms.Form):
    file = forms.FileField(required=True, help_text="Allowed file types: .txt, .md, .pdf")


@admin.register(RagSettings)
class RagSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "embedding_model",
        "chunk_size",
        "retrieval_top_k",
        "llm_model",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("embedding_model", "llm_model")
    ordering = ("-updated_at",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    change_list_template = "admin/rag_admin/document/change_list.html"
    list_display = ("id", "title", "file_name", "file_size", "created_at")
    list_filter = ("created_at",)
    search_fields = ("title", "file_name")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "upload/",
                self.admin_site.admin_view(self.upload_view),
                name="rag_admin_document_upload",
            ),
        ]
        return custom_urls + urls

    def upload_view(self, request: HttpRequest) -> HttpResponse:
        if request.method == "POST":
            form = DocumentUploadForm(request.POST, request.FILES)
            if form.is_valid():
                try:
                    handle_document_upload(form.cleaned_data["file"], request.user)
                    self.message_user(request, "Document uploaded successfully.", level=messages.SUCCESS)
                    return redirect("..")
                except ValidationError as exc:
                    self.message_user(request, f"Upload failed: {exc}", level=messages.ERROR)
                except Exception as exc:
                    self.message_user(request, f"Upload failed: {exc}", level=messages.ERROR)
        else:
            form = DocumentUploadForm()

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Upload Document",
            "form": form,
        }
        return render(request, "admin/rag_admin/document/upload.html", context)
