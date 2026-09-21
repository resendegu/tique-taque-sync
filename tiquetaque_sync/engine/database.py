"""SQLite persistence for entries, sync history, and notification deduplication."""

import sqlite3
import json
from pathlib import Path
from datetime import datetime


class Database:
    """Lightweight SQLite database manager with WAL mode for concurrent safety."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS synced_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_str TEXT NOT NULL,
                    time_str TEXT NOT NULL,
                    source TEXT,
                    approved INTEGER DEFAULT 0,
                    nsr INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date_str, time_str)
                );

                CREATE TABLE IF NOT EXISTS dispatched_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_str TEXT NOT NULL,
                    alert_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    dispatched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date_str, alert_key)
                );

                CREATE TABLE IF NOT EXISTS daily_snapshots (
                    date_str TEXT PRIMARY KEY,
                    stage TEXT NOT NULL,
                    worked_seconds INTEGER DEFAULT 0,
                    entries_json TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def record_entry(self, date_str: str, time_str: str, source: str = "web", approved: bool = False, nsr: int | None = None) -> bool:
        """Insert a synced entry. Returns True if newly inserted, False if already existed."""
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT INTO synced_entries (date_str, time_str, source, approved, nsr)
                VALUES (?, ?, ?, ?, ?)
                """,
                (date_str, time_str, source, 1 if approved else 0, nsr),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()

    def is_alert_dispatched(self, date_str: str, alert_key: str) -> bool:
        """Check if an alert was already dispatched today."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT 1 FROM dispatched_alerts WHERE date_str = ? AND alert_key = ?",
                (date_str, alert_key),
            )
            return cur.fetchone() is not None
        finally:
            conn.close()

    def mark_alert_dispatched(self, date_str: str, alert_key: str, title: str) -> None:
        """Mark an alert as dispatched to prevent duplicate spam."""
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO dispatched_alerts (date_str, alert_key, title)
                VALUES (?, ?, ?)
                """,
                (date_str, alert_key, title),
            )
            conn.commit()
        finally:
            conn.close()

    def get_entries_for_date(self, date_str: str) -> list[dict]:
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM synced_entries WHERE date_str = ? ORDER BY time_str ASC",
                (date_str,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def save_daily_snapshot(self, date_str: str, stage: str, worked_seconds: int, entries: list[dict]) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_snapshots (date_str, stage, worked_seconds, entries_json, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (date_str, stage, worked_seconds, json.dumps(entries)),
            )
            conn.commit()
        finally:
            conn.close()
