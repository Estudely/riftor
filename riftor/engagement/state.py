"""SQLite-backed engagement state: scope, hosts, services, findings, meta, activity."""

from __future__ import annotations

import json
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
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT, detail TEXT, ts REAL
);
"""

# Columns added after the original schema shipped; (table, column, type).
_LATE_COLUMNS = (
    ("findings", "cvss", "TEXT"),
    ("findings", "tags", "TEXT"),
    ("findings", "notes", "TEXT"),
    ("findings", "confidence", "INTEGER"),
    ("findings", "verification_method", "TEXT"),
)

_LATE_TABLES = """
CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement TEXT NOT NULL,
    status TEXT DEFAULT 'open',
    rationale TEXT DEFAULT '',
    evidence_ref TEXT DEFAULT '',
    created REAL,
    updated REAL
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
        for table, column, ctype in _LATE_COLUMNS:
            cols = {r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
        # activity table is created in the base schema for new DBs; ensure for old ones.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS activity "
            "(id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT, detail TEXT, ts REAL)"
        )
        self._conn.executescript(_LATE_TABLES)

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

    def service_exists(self, host: str, port: int | None, proto: str = "tcp") -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM services WHERE host=? AND IFNULL(port,-1)=IFNULL(?,-1) AND proto=? LIMIT 1",
            (host, port, proto),
        ).fetchone()
        return row is not None

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
        tags: str = "",
        notes: str = "",
        confidence: int | None = None,
        verification_method: str = "",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO findings"
            "(title, severity, host, evidence, recommendation, stage, cvss, tags, notes, "
            "confidence, verification_method, ts) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, severity, host, evidence, recommendation, stage, cvss, tags, notes,
             confidence, verification_method, time.time()),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def find_finding_id(self, title: str, host: str, severity: str, evidence: str = "") -> int | None:
        """Return the id of an existing finding matching the natural key, else None."""
        row = self._conn.execute(
            "SELECT id FROM findings WHERE "
            "LOWER(TRIM(title))=LOWER(TRIM(?)) AND LOWER(TRIM(IFNULL(host,'')))=LOWER(TRIM(?)) "
            "AND LOWER(TRIM(severity))=LOWER(TRIM(?)) AND IFNULL(evidence,'')=? LIMIT 1",
            (title, host, severity, evidence),
        ).fetchone()
        return int(row["id"]) if row else None

    def get_finding(self, finding_id: int) -> dict | None:
        row = self._conn.execute("SELECT * FROM findings WHERE id=?", (finding_id,)).fetchone()
        return dict(row) if row else None

    def update_finding(self, finding_id: int, **fields) -> bool:
        """Update allowed fields of a finding. Returns True if the row existed."""
        allowed = {
            "title", "severity", "host", "evidence", "recommendation",
            "stage", "cvss", "tags", "notes", "confidence", "verification_method",
        }
        sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not sets:
            return self.get_finding(finding_id) is not None
        cols = ", ".join(f"{k}=?" for k in sets)
        cur = self._conn.execute(
            f"UPDATE findings SET {cols} WHERE id=?", (*sets.values(), finding_id)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_finding(self, finding_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM findings WHERE id=?", (finding_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_findings(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute("SELECT * FROM findings ORDER BY id")]

    def count_findings(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS c FROM findings").fetchone()["c"])

    def find_similar(
        self, title: str, host: str = "", threshold: float = 0.75
    ) -> list[dict]:
        """Return findings with a similar title on the same host (or any host if
        *host* is empty), scored by ``difflib.SequenceMatcher``. Only entries
        at or above *threshold* are included, sorted best-match first.

        Each result dict has keys: id, title, host, severity, score.
        """
        import difflib

        rows = self._conn.execute(
            "SELECT id, title, host, severity FROM findings ORDER BY id"
        ).fetchall()
        norm_title = title.strip().lower()
        results: list[dict] = []
        for row in rows:
            if host and row["host"] and row["host"].strip().lower() != host.strip().lower():
                continue
            other = (row["title"] or "").strip().lower()
            if not other or other == norm_title:
                continue
            score = difflib.SequenceMatcher(None, norm_title, other).ratio()
            if score >= threshold:
                results.append({
                    "id": row["id"],
                    "title": row["title"],
                    "host": row["host"] or "",
                    "severity": row["severity"] or "",
                    "score": round(score, 3),
                })
        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    def correlate_findings(self) -> dict[str, list[int]]:
        """Group findings by host and by severity tag for cross-tool correlation.
        Returns ``{key: [finding_id, ...]}`` where key is e.g. ``host:10.0.0.1``
        or ``severity:critical``.
        """
        groups: dict[str, list[int]] = {}
        for row in self._conn.execute("SELECT id, host, severity, tags FROM findings"):
            fid = int(row["id"])
            h = (row["host"] or "").strip()
            s = (row["severity"] or "").strip().lower()
            tags = (row["tags"] or "").strip()
            if h:
                groups.setdefault(f"host:{h}", []).append(fid)
            if s:
                groups.setdefault(f"severity:{s}", []).append(fid)
            for tag in (t.strip() for t in tags.split(",") if t.strip()):
                groups.setdefault(f"tag:{tag}", []).append(fid)
        return {k: v for k, v in groups.items() if len(v) > 1}

    # -- hypotheses -------------------------------------------------------------
    _HYP_STATUSES = {"open", "confirmed", "refuted", "inconclusive"}

    def add_hypothesis(self, statement: str, *, status: str = "open",
                       rationale: str = "", evidence_ref: str = "") -> int:
        status = status if status in self._HYP_STATUSES else "open"
        now = time.time()
        cur = self._conn.execute(
            "INSERT INTO hypotheses(statement, status, rationale, evidence_ref, created, updated) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (statement, status, rationale, evidence_ref, now, now),
        )
        self._conn.commit()
        self.log_activity("hypothesis_add", f"#{cur.lastrowid} [{status}] {statement[:80]}")
        return int(cur.lastrowid or 0)

    def resolve_hypothesis(self, hyp_id: int, status: str, rationale: str = "") -> bool:
        if status not in self._HYP_STATUSES:
            return False
        cur = self._conn.execute(
            "UPDATE hypotheses SET status=?, rationale=?, updated=? WHERE id=?",
            (status, rationale, time.time(), hyp_id),
        )
        self._conn.commit()
        if cur.rowcount:
            self.log_activity("hypothesis_resolve", f"#{hyp_id} → {status}")
        return cur.rowcount > 0

    def list_hypotheses(self, status: str | None = None) -> list[dict]:
        if status:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM hypotheses WHERE status=? ORDER BY id", (status,))]
        return [dict(r) for r in self._conn.execute("SELECT * FROM hypotheses ORDER BY id")]

    def count_hypotheses(self, status: str = "open") -> int:
        return int(self._conn.execute(
            "SELECT COUNT(*) AS c FROM hypotheses WHERE status=?", (status,)
        ).fetchone()["c"])

    # -- activity log -----------------------------------------------------------
    def log_activity(self, event: str, detail: str = "") -> None:
        self._conn.execute(
            "INSERT INTO activity(event, detail, ts) VALUES(?, ?, ?)",
            (event, detail, time.time()),
        )
        self._conn.commit()

    def list_activity(self, limit: int = 200) -> list[dict]:
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)
            )
        ][::-1]

    # -- export -----------------------------------------------------------------
    def export_dict(self) -> dict:
        """A JSON-serializable snapshot of the whole engagement state."""
        return {
            "meta": {r["key"]: r["value"] for r in self._conn.execute("SELECT key, value FROM meta")},
            "scope": [{"target": t, "mode": m} for t, m in self.list_scope()],
            "hosts": self.list_hosts(),
            "services": self.list_services(),
            "findings": self.list_findings(),
            "activity": self.list_activity(limit=10_000),
        }

    def dump_json(self) -> str:
        return json.dumps(self.export_dict(), indent=2)

    def close(self) -> None:
        self._conn.close()
