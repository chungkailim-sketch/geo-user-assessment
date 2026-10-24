"""Tiny SQLite-backed JSON cache with per-read TTLs."""

import json
import sqlite3
import threading
import time
from pathlib import Path


class JsonCache:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created REAL NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str, ttl_seconds: float) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT value, created FROM cache WHERE key = ?", (key,)).fetchone()
        if not row or time.time() - row[1] > ttl_seconds:
            return None
        return json.loads(row[0])

    def set(self, key: str, value: dict, keep_created: bool = False) -> None:
        """Store `value`. With keep_created, an update doesn't extend the entry's TTL."""
        payload = json.dumps(value, default=str)
        with self._lock:
            row = self._conn.execute("SELECT created FROM cache WHERE key = ?", (key,)).fetchone()
            created = row[0] if keep_created and row else time.time()
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, created) VALUES (?, ?, ?)", (key, payload, created)
            )
            self._conn.commit()

    def purge_older_than(self, max_age_seconds: float) -> int:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM cache WHERE created < ?", (time.time() - max_age_seconds,))
            self._conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
