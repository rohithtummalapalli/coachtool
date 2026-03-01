from __future__ import annotations

from pathlib import Path

from rag.store import store_document


def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 150) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size")

    chunks: list[str] = []
    start = 0
    length = len(text)

    while start < length:
        end = min(start + chunk_size, length)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap

    return chunks


def ingest_file(filepath: str) -> None:
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    text = path.read_text(encoding="utf-8")
    for chunk in _chunk_text(text):
        store_document(chunk)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest a text file into pgvector store.")
    parser.add_argument("filepath", type=str, help="Path to a UTF-8 text file")
    args = parser.parse_args()
    ingest_file(args.filepath)

