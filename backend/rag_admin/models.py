from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q


class RagSettings(models.Model):
    embedding_model = models.CharField(max_length=255, default="sentence-transformers/all-MiniLM-L6-v2")
    chunk_size = models.PositiveIntegerField(default=1000)
    chunk_overlap = models.PositiveIntegerField(default=150)
    retrieval_top_k = models.PositiveIntegerField(default=3)
    similarity_threshold = models.FloatField(default=0.0)
    llm_model = models.CharField(max_length=255, default="gpt-4o-mini")
    temperature = models.FloatField(default=0.2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=Q(is_active=True),
                name="unique_active_rag_settings",
            ),
            models.CheckConstraint(condition=Q(chunk_size__gt=0), name="chunk_size_gt_0"),
            models.CheckConstraint(condition=Q(chunk_overlap__gte=0), name="chunk_overlap_gte_0"),
            models.CheckConstraint(condition=Q(retrieval_top_k__gt=0), name="retrieval_top_k_gt_0"),
            models.CheckConstraint(condition=Q(temperature__gte=0.0) & Q(temperature__lte=2.0), name="temperature_between_0_2"),
        ]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_active:
            RagSettings.objects.exclude(pk=self.pk).filter(is_active=True).update(is_active=False)

    @classmethod
    def get_active_settings(cls) -> "RagSettings":
        settings_obj = cls.objects.filter(is_active=True).order_by("-updated_at").first()
        if settings_obj:
            return settings_obj
        return cls.objects.create(is_active=True)

    def __str__(self) -> str:
        return f"RAG Settings ({'active' if self.is_active else 'inactive'})"


class Document(models.Model):
    title = models.CharField(max_length=255)
    content = models.TextField()
    file_name = models.CharField(max_length=255)
    file_size = models.PositiveBigIntegerField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["title"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self) -> str:
        return self.title
