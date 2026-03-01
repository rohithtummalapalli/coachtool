from __future__ import annotations

from pgvector.psycopg2 import Vector

from rag.embeddings import generate_embedding
from services.database import db_connection


def retrieve_documents(query: str, limit: int = 3) -> list[str]:
    embedding = generate_embedding(query)
    sql = """
        SELECT content
        FROM documents
        ORDER BY embedding <-> %s
        LIMIT %s
    """

    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (Vector(embedding), limit))
            rows = cur.fetchall()

    return [row[0] for row in rows]

