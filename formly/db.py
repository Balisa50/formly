"""Database layer — uses PostgreSQL (Supabase) in production, SQLite locally."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

from .config import DB_PATH

DATABASE_URL = os.getenv("DATABASE_URL", "")

# ─── Detect database mode ──────────────────────────────
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg
    from psycopg.rows import dict_row

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    category   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_experience (
    id          SERIAL PRIMARY KEY,
    company     TEXT,
    title       TEXT,
    start_date  TEXT,
    end_date    TEXT,
    description TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS education (
    id          SERIAL PRIMARY KEY,
    institution TEXT,
    degree      TEXT,
    field       TEXT,
    start_date  TEXT,
    end_date    TEXT,
    gpa         TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    category    TEXT,
    proficiency TEXT
);

CREATE TABLE IF NOT EXISTS essays (
    id         SERIAL PRIMARY KEY,
    prompt     TEXT NOT NULL,
    context    TEXT,
    response   TEXT NOT NULL,
    approved   INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id           SERIAL PRIMARY KEY,
    url          TEXT NOT NULL,
    title        TEXT,
    status       TEXT DEFAULT 'draft',
    fields_json  TEXT,
    submitted_at TEXT,
    created_at   TEXT NOT NULL
);
"""

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    category   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS work_experience (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company     TEXT,
    title       TEXT,
    start_date  TEXT,
    end_date    TEXT,
    description TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS education (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    institution TEXT,
    degree      TEXT,
    field       TEXT,
    start_date  TEXT,
    end_date    TEXT,
    gpa         TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    category    TEXT,
    proficiency TEXT
);

CREATE TABLE IF NOT EXISTS essays (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt     TEXT NOT NULL,
    context    TEXT,
    response   TEXT NOT NULL,
    approved   INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT NOT NULL,
    title        TEXT,
    status       TEXT DEFAULT 'draft',
    fields_json  TEXT,
    submitted_at TEXT,
    created_at   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Connection helpers ────────────────────────────────

@contextmanager
def get_conn():
    """Yields a DB connection (PostgreSQL or SQLite) with auto-commit."""
    if USE_POSTGRES:
        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def _execute(conn, sql: str, params: tuple = ()):
    """Execute a query, adapting ? to %s for PostgreSQL."""
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
        # Handle INSERT OR REPLACE -> INSERT ... ON CONFLICT
        if "INSERT OR REPLACE" in sql.upper():
            sql = _pg_upsert(sql)
        # Handle INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
        if "INSERT OR IGNORE" in sql.upper():
            sql = sql.replace("INSERT OR IGNORE", "INSERT").replace("insert or ignore", "INSERT")
            sql = sql.rstrip(";").rstrip() + " ON CONFLICT DO NOTHING"
    cur = conn.execute(sql, params)
    return cur


def _pg_upsert(sql: str) -> str:
    """Convert INSERT OR REPLACE INTO table (...) VALUES (...) to PostgreSQL upsert."""
    sql = sql.replace("INSERT OR REPLACE", "INSERT")
    # Extract table name and columns
    import re
    match = re.match(r"INSERT\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)", sql, re.IGNORECASE)
    if match:
        table = match.group(1)
        cols = match.group(2)
        vals = match.group(3)
        col_list = [c.strip() for c in cols.split(",")]
        update_parts = [f"{c} = EXCLUDED.{c}" for c in col_list if c != "key" and c != "id"]
        return f"INSERT INTO {table} ({cols}) VALUES ({vals}) ON CONFLICT (key) DO UPDATE SET {', '.join(update_parts)}"
    return sql


def _fetchall(conn, sql: str, params: tuple = ()) -> list[dict]:
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
        cur = conn.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    else:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def _fetchone(conn, sql: str, params: tuple = ()) -> dict | None:
    if USE_POSTGRES:
        sql = sql.replace("?", "%s")
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None
    else:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def init_db() -> None:
    try:
        if USE_POSTGRES:
            conn = psycopg.connect(DATABASE_URL, autocommit=True)
            try:
                for statement in PG_SCHEMA.strip().split(";"):
                    statement = statement.strip()
                    if statement:
                        conn.execute(statement + ";")
            finally:
                conn.close()
        else:
            conn = sqlite3.connect(str(DB_PATH))
            conn.executescript(SQLITE_SCHEMA)
            conn.close()
    except Exception as e:
        print(f"[db] init_db warning: {e} — tables will be created on first request")


# ─── Profile ────────────────────────────────────────────

def set_profile(key: str, value: str, category: str = "custom") -> None:
    with get_conn() as conn:
        _execute(conn,
            "INSERT OR REPLACE INTO profile (key, value, category, updated_at) VALUES (?, ?, ?, ?)",
            (key.lower().strip(), value.strip(), category, _now()),
        )


def get_profile(key: str) -> str | None:
    with get_conn() as conn:
        row = _fetchone(conn, "SELECT value FROM profile WHERE key = ?", (key.lower().strip(),))
    return row["value"] if row else None


def get_all_profile() -> dict[str, str]:
    with get_conn() as conn:
        rows = _fetchall(conn, "SELECT key, value FROM profile ORDER BY category, key")
    return {r["key"]: r["value"] for r in rows}


def get_profile_by_category() -> dict[str, dict[str, str]]:
    with get_conn() as conn:
        rows = _fetchall(conn, "SELECT key, value, category FROM profile ORDER BY category, key")
    result: dict[str, dict[str, str]] = {}
    for r in rows:
        result.setdefault(r["category"], {})[r["key"]] = r["value"]
    return result


def delete_profile(key: str) -> None:
    with get_conn() as conn:
        _execute(conn, "DELETE FROM profile WHERE key = ?", (key.lower().strip(),))


# ─── Work Experience ────────────────────────────────────

def add_work(company: str, title: str, start_date: str = "", end_date: str = "", description: str = "") -> int:
    with get_conn() as conn:
        if USE_POSTGRES:
            row = conn.execute(
                "INSERT INTO work_experience (company, title, start_date, end_date, description, created_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                (company, title, start_date, end_date, description, _now()),
            ).fetchone()
            return row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO work_experience (company, title, start_date, end_date, description, created_at) VALUES (?,?,?,?,?,?)",
                (company, title, start_date, end_date, description, _now()),
            )
            return cur.lastrowid  # type: ignore


def get_all_work() -> list[dict]:
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM work_experience ORDER BY start_date DESC")


def delete_work(id: int) -> None:
    with get_conn() as conn:
        _execute(conn, "DELETE FROM work_experience WHERE id = ?", (id,))


# ─── Education ──────────────────────────────────────────

def add_education(institution: str, degree: str, field: str = "", start_date: str = "", end_date: str = "", gpa: str = "") -> int:
    with get_conn() as conn:
        if USE_POSTGRES:
            row = conn.execute(
                "INSERT INTO education (institution, degree, field, start_date, end_date, gpa, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (institution, degree, field, start_date, end_date, gpa, _now()),
            ).fetchone()
            return row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO education (institution, degree, field, start_date, end_date, gpa, created_at) VALUES (?,?,?,?,?,?,?)",
                (institution, degree, field, start_date, end_date, gpa, _now()),
            )
            return cur.lastrowid  # type: ignore


def get_all_education() -> list[dict]:
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM education ORDER BY start_date DESC")


def delete_education(id: int) -> None:
    with get_conn() as conn:
        _execute(conn, "DELETE FROM education WHERE id = ?", (id,))


# ─── Skills ─────────────────────────────────────────────

def add_skill(name: str, category: str = "technical", proficiency: str = "intermediate") -> None:
    with get_conn() as conn:
        _execute(conn,
            "INSERT OR IGNORE INTO skills (name, category, proficiency) VALUES (?, ?, ?)",
            (name.strip(), category, proficiency),
        )


def get_all_skills() -> list[dict]:
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM skills ORDER BY category, name")


def delete_skill(id: int) -> None:
    with get_conn() as conn:
        _execute(conn, "DELETE FROM skills WHERE id = ?", (id,))


# ─── Essays ─────────────────────────────────────────────

def save_essay(prompt: str, response: str, context: str = "", approved: bool = False) -> int:
    with get_conn() as conn:
        if USE_POSTGRES:
            row = conn.execute(
                "INSERT INTO essays (prompt, context, response, approved, created_at) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (prompt, context, response, int(approved), _now()),
            ).fetchone()
            return row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO essays (prompt, context, response, approved, created_at) VALUES (?,?,?,?,?)",
                (prompt, context, response, int(approved), _now()),
            )
            return cur.lastrowid  # type: ignore


def get_past_essays(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM essays ORDER BY created_at DESC LIMIT ?", (limit,))


# ─── Applications ───────────────────────────────────────

def log_application(url: str, title: str = "", fields: dict | None = None) -> int:
    with get_conn() as conn:
        if USE_POSTGRES:
            row = conn.execute(
                "INSERT INTO applications (url, title, fields_json, created_at) VALUES (%s,%s,%s,%s) RETURNING id",
                (url, title, json.dumps(fields or {}), _now()),
            ).fetchone()
            return row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO applications (url, title, fields_json, created_at) VALUES (?,?,?,?)",
                (url, title, json.dumps(fields or {}), _now()),
            )
            return cur.lastrowid  # type: ignore


def update_application(app_id: int, status: str, fields: dict | None = None) -> None:
    with get_conn() as conn:
        if fields is not None:
            _execute(conn,
                "UPDATE applications SET status = ?, fields_json = ?, submitted_at = ? WHERE id = ?",
                (status, json.dumps(fields), _now() if status == "submitted" else None, app_id),
            )
        else:
            _execute(conn,
                "UPDATE applications SET status = ?, submitted_at = ? WHERE id = ?",
                (status, _now() if status == "submitted" else None, app_id),
            )


def get_all_applications() -> list[dict]:
    with get_conn() as conn:
        return _fetchall(conn, "SELECT * FROM applications ORDER BY created_at DESC")


# ─── Full profile export (for matcher/essay writer) ────

def get_full_profile() -> dict:
    """Return the entire profile as a single dict for LLM consumption."""
    return {
        "personal": get_all_profile(),
        "work_experience": get_all_work(),
        "education": get_all_education(),
        "skills": get_all_skills(),
        "past_essays": get_past_essays(5),
    }


# Auto-create schema on import
init_db()
