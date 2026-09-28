"""Cache persistente em SQLite com TTL (consultas de busca e metadados)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable


def make_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class SQLiteCache:
    """Cache chave/valor JSON com expiração.

    Thread-safe (uma conexão por instância protegida por lock) e resistente a
    corrupção: se o arquivo não puder ser aberto, cai para um banco em memória
    em vez de derrubar o pipeline.
    """

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._init()
        except sqlite3.DatabaseError:
            self._conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._init()

    def _init(self) -> None:
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS cache ("
                " namespace TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,"
                " expires_at REAL NOT NULL, PRIMARY KEY (namespace, key))"
            )

    def get(self, namespace: str, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM cache WHERE namespace=? AND key=?", (namespace, key)
            ).fetchone()
            if row is None:
                return None
            value, expires_at = row
            if expires_at < self._clock():
                with self._conn:
                    self._conn.execute("DELETE FROM cache WHERE namespace=? AND key=?", (namespace, key))
                return None
        return json.loads(value)

    def set(self, namespace: str, key: str, value: Any, ttl_seconds: float) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache(namespace, key, value, expires_at) VALUES (?,?,?,?)",
                (namespace, key, payload, self._clock() + ttl_seconds),
            )

    def purge_expired(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM cache WHERE expires_at < ?", (self._clock(),))
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()
