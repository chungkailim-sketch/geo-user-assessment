"""History of audits and reports that have been run, kept next to the cache in the same SQLite file.

One row per run: what was audited, the scores, how long it took and whether a saved result was reused.
No visitor data (IP or email address) is stored.
"""

import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class RunRecord:
    kind: str  # "audit" | "report"
    domain: str
    brand: str = ""
    score: float | None = None
    seconds: float = 0.0
    cached: bool = False  # served from a saved result instead of being recomputed
    status: str = "ok"  # ok | error | sent
    created: float = 0.0


class RunLog:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
            "domain TEXT NOT NULL, brand TEXT, score REAL, seconds REAL, cached INTEGER, status TEXT, created REAL)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS runs_created ON runs (created)")
        self._conn.commit()

    def add(self, record: RunRecord) -> None:
        row = asdict(record)
        row["created"] = row["created"] or time.time()
        row["cached"] = int(row["cached"])
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (kind, domain, brand, score, seconds, cached, status, created) "
                "VALUES (:kind, :domain, :brand, :score, :seconds, :cached, :status, :created)", row,
            )
            self._conn.commit()

    def recent(self, limit: int = 50, kind: str | None = None) -> list[RunRecord]:
        query = "SELECT kind, domain, brand, score, seconds, cached, status, created FROM runs"
        args: tuple = ()
        if kind:
            query += " WHERE kind = ?"
            args = (kind,)
        with self._lock:
            rows = self._conn.execute(query + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [RunRecord(r[0], r[1], r[2] or "", r[3], r[4] or 0.0, bool(r[5]), r[6] or "ok", r[7]) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
