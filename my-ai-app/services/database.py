from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

import psycopg2
from dotenv import load_dotenv
from pgvector.psycopg2 import register_vector
from psycopg2.extensions import connection as PGConnection


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")


def _db_config() -> dict[str, str]:
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": os.getenv("DB_PORT", "5432"),
        "dbname": os.getenv("DB_NAME", "my_ai_app"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
    }


def get_connection() -> PGConnection:
    conn = psycopg2.connect(**_db_config())
    register_vector(conn)
    return conn


@contextmanager
def db_connection() -> Generator[PGConnection, None, None]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def initialize_database() -> None:
    ddl = """
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE TABLE IF NOT EXISTS documents (
        id SERIAL PRIMARY KEY,
        content TEXT NOT NULL,
        embedding VECTOR(384)
    );
    """
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(ddl)
        conn.commit()

