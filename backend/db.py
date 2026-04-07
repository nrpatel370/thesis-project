"""
Database helpers for the Grade Normalizer backend.

All tables are created in init_db(), which is called once at server start-up.
sqlite3.Row is set as the row factory so columns can be accessed by name
in addition to index.

Tables:
  user_preferences  — selected column checkboxes and dropdown choices per CRN
  user_categories   — custom keyword-to-category mappings per CRN
  uploaded_csvs     — the most recent CSV upload per CRN (older rows pruned on insert)
  course_config     — per-CRN formula weights; empty dict signals "use defaults"
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "grades.db")


def get_db():
    """Open and return a connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they do not already exist. Safe to call on every startup."""
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                course_id   TEXT,
                preferences TEXT    NOT NULL,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_categories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                course_id   TEXT,
                categories  TEXT    NOT NULL,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, course_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS uploaded_csvs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL,
                csv_data    TEXT    NOT NULL,
                uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS course_config (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT    NOT NULL UNIQUE,
                config      TEXT    NOT NULL,
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def get_latest_uploaded_csv(user_id):
    """Return the raw CSV text for the most recent upload by this user, or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT csv_data FROM uploaded_csvs WHERE user_id = ? ORDER BY uploaded_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return row["csv_data"] if row else None
