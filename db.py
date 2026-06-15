import os

import psycopg2
from psycopg2.extras import RealDictCursor

_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)


def _conn():
    return psycopg2.connect(_URL, cursor_factory=RealDictCursor)


def init_db():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id         SERIAL PRIMARY KEY,
                    title      TEXT        NOT NULL,
                    body       TEXT        NOT NULL,
                    author     TEXT        NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ
                )
            """)


def get_posts() -> list:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM posts ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]


def get_post(post_id: int) -> dict | None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM posts WHERE id = %s", (post_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def create_post(title: str, body: str, author: str) -> dict:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO posts (title, body, author) VALUES (%s, %s, %s) RETURNING *",
                (title, body, author),
            )
            return dict(cur.fetchone())


def update_post(post_id: int, title: str, body: str) -> dict | None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE posts SET title=%s, body=%s, updated_at=NOW() WHERE id=%s RETURNING *",
                (title, body, post_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def delete_post(post_id: int) -> bool:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM posts WHERE id=%s RETURNING id", (post_id,))
            return cur.fetchone() is not None
