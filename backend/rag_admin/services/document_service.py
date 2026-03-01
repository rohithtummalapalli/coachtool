from __future__ import annotations

import importlib
import os
import tempfile
from typing import Callable

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from rag_admin.models import Document


ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf"}
DEFAULT_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

User = get_user_model()


def _validate_file(file_obj) -> None:
    name = getattr(file_obj, "name", "")
    _, ext = os.path.splitext(name.lower())
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError("Unsupported file type. Allowed: .txt, .md, .pdf")

    max_size = int(os.getenv("RAG_ADMIN_MAX_FILE_SIZE_BYTES", DEFAULT_MAX_FILE_SIZE_BYTES))
    size = int(getattr(file_obj, "size", 0))
    if size <= 0:
        raise ValidationError("Uploaded file is empty.")
    if size > max_size:
        raise ValidationError(f"File too large. Max allowed is {max_size} bytes.")


def _read_file_content(file_obj) -> str:
    file_obj.seek(0)
    raw = file_obj.read()
    if isinstance(raw, str):
        return raw

    name = getattr(file_obj, "name", "")
    _, ext = os.path.splitext(name.lower())
    if ext in {".txt", ".md"}:
        return raw.decode("utf-8", errors="ignore")

    if ext == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore

            file_obj.seek(0)
            reader = PdfReader(file_obj)
            return "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception:
            return raw.decode("latin-1", errors="ignore")

    return raw.decode("utf-8", errors="ignore")


def _get_ingest_callable() -> Callable[[str], None]:
    dotted_path = os.getenv("RAG_INGEST_FUNCTION", "rag.ingest.ingest_file")
    module_name, function_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    fn = getattr(module, function_name, None)
    if fn is None or not callable(fn):
        raise RuntimeError(f"Ingestion function not found: {dotted_path}")
    return fn


@transaction.atomic
def handle_document_upload(file, user: User | None) -> Document:
    _validate_file(file)
    content = _read_file_content(file)
    if not content.strip():
        raise ValidationError("Uploaded file has no readable content.")

    file_name = getattr(file, "name", "uploaded-file")
    file_size = int(getattr(file, "size", 0))
    title = os.path.splitext(os.path.basename(file_name))[0]
    title = title[:255] or "Untitled Document"

    document = Document.objects.create(
        title=title,
        content=content,
        file_name=file_name[:255],
        file_size=file_size,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )

    ingest_file = _get_ingest_callable()
    _, ext = os.path.splitext(file_name)
    safe_suffix = ext if ext.lower() in ALLOWED_EXTENSIONS else ".txt"

    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=safe_suffix, delete=False) as temp:
            temp.write(content)
            temp_path = temp.name

        ingest_file(temp_path)
    except Exception:
        document.delete()
        raise
    finally:
        if "temp_path" in locals() and os.path.exists(temp_path):
            os.remove(temp_path)

    return document
