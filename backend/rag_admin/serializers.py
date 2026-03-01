from __future__ import annotations

from rest_framework import serializers

from rag_admin.models import Document, RagSettings


class RagSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = RagSettings
        fields = [
            "id",
            "embedding_model",
            "chunk_size",
            "chunk_overlap",
            "retrieval_top_k",
            "similarity_threshold",
            "llm_model",
            "temperature",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate(self, attrs):
        chunk_size = attrs.get("chunk_size", getattr(self.instance, "chunk_size", 1000))
        chunk_overlap = attrs.get("chunk_overlap", getattr(self.instance, "chunk_overlap", 150))
        if chunk_overlap >= chunk_size:
            raise serializers.ValidationError("chunk_overlap must be less than chunk_size")
        return attrs


class DocumentSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)

    class Meta:
        model = Document
        fields = [
            "id",
            "title",
            "content",
            "file_name",
            "file_size",
            "created_by",
            "created_by_email",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_by", "created_by_email", "created_at", "updated_at"]


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField(required=True)
