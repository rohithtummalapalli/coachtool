from __future__ import annotations

from sentence_transformers import SentenceTransformer


EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_model = SentenceTransformer(EMBEDDING_MODEL_NAME)


def generate_embedding(text: str) -> list[float]:
    vector = _model.encode(text, normalize_embeddings=True)
    return vector.tolist()

