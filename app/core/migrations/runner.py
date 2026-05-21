"""
migrations/runner.py — Idempotent SQLite migration runner.

Reads numbered SQL files from the migrations directory and executes them
in order. Tracks applied migrations in a _migrations table.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("wiki.migrations")

MIGRATIONS_DIR = Path(__file__).parent


def run_migrations(db_path: Path) -> None:
    """Apply all pending migrations to the SQLite database.

    Creates the database file and parent directories if they don't exist.
    Safe to call multiple times — already-applied migrations are skipped.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    try:
        _apply_pragmas(conn)
        _ensure_migrations_table(conn)
        applied = _get_applied_migrations(conn)

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for mf in migration_files:
            migration_id = _extract_migration_id(mf)
            if migration_id in applied:
                continue

            logger.info("Applying migration: %s", mf.name)
            sql = mf.read_text(encoding="utf-8")
            conn.executescript(sql)
            _mark_applied(conn, migration_id)
            conn.commit()
            logger.info("Migration applied: %s", mf.name)
    finally:
        conn.close()


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY,
            migration_id TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _get_applied_migrations(conn: sqlite3.Connection) -> set[str]:
    cursor = conn.execute("SELECT migration_id FROM _migrations ORDER BY id")
    return {row[0] for row in cursor.fetchall()}


def _mark_applied(conn: sqlite3.Connection, migration_id: str) -> None:
    conn.execute(
        "INSERT INTO _migrations (migration_id) VALUES (?)",
        (migration_id,),
    )


def _extract_migration_id(filepath: Path) -> str:
    """Extract migration ID from filename, e.g. '001_core_schema' from '001_core_schema.sql'."""
    return filepath.stem
