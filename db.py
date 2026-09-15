"""
Thin PostgreSQL access layer using psycopg2.
"""
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from config import DATABASE_URL

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


@contextmanager
def get_conn():
    if not DATABASE_URL or "localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing or points to localhost. Add a hosted PostgreSQL "
            "connection string under Streamlit Cloud > Manage app > Settings > Secrets."
        )
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't already exist. Safe to call repeatedly."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            with open(_SCHEMA_PATH, "r") as f:
                cur.execute(f.read())

            cur.execute(
                "ALTER TABLE generation_batches ADD COLUMN IF NOT EXISTS used_clauses JSONB"
            )


def fetch_all(query, params=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            return cur.fetchall()


def fetch_one(query, params=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            return cur.fetchone()


def execute(query, params=None, returning=False):
    """
    Run an INSERT/UPDATE/DELETE. If returning=True, the query must include a
    RETURNING clause and the resulting single row (as a dict) is returned.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params or ())
            if returning:
                return cur.fetchone()
