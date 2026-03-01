from __future__ import annotations

from typing import Any

from django.db import transaction

from rag_admin.models import RagSettings


def get_active_settings() -> RagSettings:
    return RagSettings.get_active_settings()


@transaction.atomic
def update_settings(data: dict[str, Any]) -> RagSettings:
    active = RagSettings.get_active_settings()
    for field in [
        "embedding_model",
        "chunk_size",
        "chunk_overlap",
        "retrieval_top_k",
        "similarity_threshold",
        "llm_model",
        "temperature",
        "is_active",
    ]:
        if field in data:
            setattr(active, field, data[field])

    if active.chunk_overlap >= active.chunk_size:
        raise ValueError("chunk_overlap must be less than chunk_size")

    active.save()
    if active.is_active:
        RagSettings.objects.exclude(pk=active.pk).filter(is_active=True).update(is_active=False)
    return active
