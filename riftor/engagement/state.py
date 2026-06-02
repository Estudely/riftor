"""SQLite-backed engagement state: scope, hosts, services, findings, meta."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS scope (target TEXT, mode TEXT, UNIQUE(target, mode));
CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY, note TEXT, first_seen REAL);
CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT, port INTEGER, proto TEXT, service TEXT, version TEXT, note TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, severity TEXT, host TEXT, evidence TEXT, recommendation TEXT, stage TEXT, ts REAL
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(findings)")}
        if "cvss" not in cols:
            self._conn.execute("ALTER TABLE findings ADD COLUMN cvss TEXT")

    # -- meta -------------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self._conn.commit()

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    # -- scope ------------------------------------------------------------------
    def add_scope(self, target: str, mode: str) -> None:
        self._conn.execute("INSERT OR IGNORE INTO scope(target, mode) VALUES(?, ?)", (target, mode))
        self._conn.commit()

    def remove_scope(self, target: str) -> None:
        self._conn.execute("DELETE FROM scope WHERE target=?", (target,))
        self._conn.commit()

    def clear_scope(self) -> None:
        self._conn.execute("DELETE FROM scope")
        self._conn.commit()

    def list_scope(self) -> list[tuple[str, str]]:
        return [(r["target"], r["mode"]) for r in self._conn.execute("SELECT target, mode FROM scope")]

    # -- hosts / services -------------------------------------------------------
    def add_host(self, host: str, note: str = "") -> None:
        self._conn.execute(
            "INSERT INTO hosts(host, note, first_seen) VALUES(?, ?, ?) "
            "ON CONFLICT(host) DO UPDATE SET note=excluded.note",
            (host, note, time.time()),
        )
        self._conn.commit()

    def add_service(
        self,
        host: str,
        port: int | None = None,
        proto: str = "tcp",
        service: str = "",
        version: str = "",
        note: str = "",
    ) -> int:
        self.add_host(host)
        cur = self._conn.execute(
            "INSERT INTO services(host, port, proto, service, version, note, ts) "
            "VALUES(?, ?, ?, ?, ?, ?, ?)",
            (host, port, proto, service, version, note, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_services(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM services ORDER BY host, port")]

    def list_hosts(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM hosts ORDER BY host")]

    # -- findings ---------------------------------------------------------------
    def add_finding(
        self,
        title: str,
        severity: str,
        host: str = "",
        evidence: str = "",
        recommendation: str = "",
        stage: str = "",
        cvss: str = "",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO findings(title, severity, host, evidence, recommendation, stage, cvss, ts) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (title, severity, host, evidence, recommendation, stage, cvss, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_findings(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM findings ORDER BY id")]

    def count_findings(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS c FROM findings").fetchone()["c"])

    def close(self) -> None:
        self._conn.close()
