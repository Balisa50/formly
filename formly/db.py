"""SQLite profile database — grows smarter with every form filled."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH

SCHEMA = """
-- Key-value profile store (absorbs any field type)
CREATE TABLE IF NOT EXISTS profile (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    category   TEXT NOT NULL,  -- personal, education, work, skills, custom
    updated_at TEXT NOT NULL
);

-- Work experience entries
CREATE TABLE IF NOT EXISTS work_experience (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company     TEXT,
    title       TEXT,
    start_date  TEXT,
    end_date    TEXT,
    description TEXT,
    created_at  TEXT NOT NULL
);

-- Education entries
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

-- Skills
CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    category    TEXT,
    proficiency TEXT
);

-- Generated essays
CREATE TABLE IF NOT EXISTS essays (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt     TEXT NOT NULL,
    context    TEXT,
    response   TEXT NOT NULL,
    approved   INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Application log
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


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ─── Profile ────────────────────────────────────────────

def set_profile(key: str, value: str, category: str = "custom") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO profile (key, value, category, updated_at) VALUES (?, ?, ?, ?)",
            (key.lower().strip(), value.strip(), category, _now()),
        )


def get_profile(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM profile WHERE key = ?", (key.lower().strip(),)).fetchone()
    return row["value"] if row else None


def get_all_profile() -> dict[str, str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM profile ORDER BY category, key").fetchall()
    return {r["key"]: r["value"] for r in rows}


def get_profile_by_category() -> dict[str, dict[str, str]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value, category FROM profile ORDER BY category, key").fetchall()
    result: dict[str, dict[str, str]] = {}
    for r in rows:
        result.setdefault(r["category"], {})[r["key"]] = r["value"]
    return result


def delete_profile(key: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM profile WHERE key = ?", (key.lower().strip(),))


# ─── Work Experience ────────────────────────────────────

def add_work(company: str, title: str, start_date: str = "", end_date: str = "", description: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO work_experience (company, title, start_date, end_date, description, created_at) VALUES (?,?,?,?,?,?)",
            (company, title, start_date, end_date, description, _now()),
        )
        return cur.lastrowid  # type: ignore


def get_all_work() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM work_experience ORDER BY start_date DESC").fetchall()
    return [dict(r) for r in rows]


def delete_work(id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM work_experience WHERE id = ?", (id,))


# ─── Education ──────────────────────────────────────────

def add_education(institution: str, degree: str, field: str = "", start_date: str = "", end_date: str = "", gpa: str = "") -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO education (institution, degree, field, start_date, end_date, gpa, created_at) VALUES (?,?,?,?,?,?,?)",
            (institution, degree, field, start_date, end_date, gpa, _now()),
        )
        return cur.lastrowid  # type: ignore


def get_all_education() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM education ORDER BY start_date DESC").fetchall()
    return [dict(r) for r in rows]


def delete_education(id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM education WHERE id = ?", (id,))


# ─── Skills ─────────────────────────────────────────────

def add_skill(name: str, category: str = "technical", proficiency: str = "intermediate") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO skills (name, category, proficiency) VALUES (?, ?, ?)",
            (name.strip(), category, proficiency),
        )


def get_all_skills() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM skills ORDER BY category, name").fetchall()
    return [dict(r) for r in rows]


def delete_skill(id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM skills WHERE id = ?", (id,))


# ─── Essays ─────────────────────────────────────────────

def save_essay(prompt: str, response: str, context: str = "", approved: bool = False) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO essays (prompt, context, response, approved, created_at) VALUES (?,?,?,?,?)",
            (prompt, context, response, int(approved), _now()),
        )
        return cur.lastrowid  # type: ignore


def get_past_essays(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM essays ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Applications ───────────────────────────────────────

def log_application(url: str, title: str = "", fields: dict | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO applications (url, title, fields_json, created_at) VALUES (?,?,?,?)",
            (url, title, json.dumps(fields or {}), _now()),
        )
        return cur.lastrowid  # type: ignore


def update_application(app_id: int, status: str, fields: dict | None = None) -> None:
    with get_conn() as conn:
        if fields is not None:
            conn.execute(
                "UPDATE applications SET status = ?, fields_json = ?, submitted_at = ? WHERE id = ?",
                (status, json.dumps(fields), _now() if status == "submitted" else None, app_id),
            )
        else:
            conn.execute(
                "UPDATE applications SET status = ?, submitted_at = ? WHERE id = ?",
                (status, _now() if status == "submitted" else None, app_id),
            )


def get_all_applications() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM applications ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


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
