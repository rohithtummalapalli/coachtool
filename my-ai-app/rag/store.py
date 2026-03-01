from __future__ import annotations

from pgvector.psycopg2 import Vector

from rag.embeddings import generate_embedding
from services.database import db_connection


def store_document(text: str) -> None:
    embedding = generate_embedding(text)
    sql = "INSERT INTO documents (content, embedding) VALUES (%s, %s)"

    with db_connection() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (text, Vector(embedding)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

