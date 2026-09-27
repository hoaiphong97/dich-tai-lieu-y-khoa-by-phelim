"""Bộ nhớ đệm bản dịch (SQLite): dịch lại hoặc tiếp tục job bị ngắt không tốn thêm thời gian."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime
from pathlib import Path


class TranslationCache:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, dst TEXT NOT NULL, created TEXT NOT NULL)"
        )
        self._conn.commit()

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT dst FROM translations WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, dst: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO translations (key, dst, created) VALUES (?, ?, ?)",
                (key, dst, datetime.now().isoformat(timespec="seconds")),
            )
            self._conn.commit()

    def clear(self) -> int:
        with self._lock:
            count = self._conn.execute("SELECT COUNT(*) FROM translations").fetchone()[0]
            self._conn.execute("DELETE FROM translations")
            self._conn.commit()
        return count
