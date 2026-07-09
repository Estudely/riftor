"""Merge engagement state from another SQLite DB into the current engagement.

Collaborative MVP (#50): operators share a workdir or hand off an
``engagement.db`` / export zip; this module imports missing scope/hosts/
services/findings without clobbering local rows (skip-on-conflict).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class MergeStats:
    scope_in: int = 0
    scope_out: int = 0
    hosts: int = 0
    services: int = 0
    findings: int = 0
    skipped_findings: int = 0
    source: str = ""
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [
            f"+{self.scope_in} in-scope",
            f"+{self.scope_out} out-of-scope",
            f"+{self.hosts} hosts",
            f"+{self.services} services",
            f"+{self.findings} findings",
        ]
        if self.skipped_findings:
            parts.append(f"{self.skipped_findings} findings already present")
        return ", ".join(parts)


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def merge_engagement_db(engagement, source: Path) -> MergeStats:
    """Import rows from ``source`` (an engagement.db) into ``engagement``.

    Conflict policy:
    - scope / hosts: INSERT OR IGNORE semantics via existing add_* helpers
    - services: skip if (host, port, proto) already exists
    - findings: skip if natural key (title, host, severity, evidence) matches
    """
    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"no engagement db at {path}")

    stats = MergeStats(source=str(path))
    conn = _open_readonly(path)
    try:
        # Detect a real engagement schema (missing tables => wrong file).
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "scope" not in tables and "findings" not in tables:
            raise ValueError(f"{path} does not look like a riftor engagement.db")

        if "scope" in tables:
            for row in conn.execute("SELECT target, mode FROM scope"):
                mode = (row["mode"] or "in").lower()
                engagement.add_scope(row["target"], mode)
                if mode == "out":
                    stats.scope_out += 1
                else:
                    stats.scope_in += 1

        if "hosts" in tables:
            before = {h["host"] for h in engagement.store.list_hosts()}
            for row in conn.execute("SELECT host, note FROM hosts"):
                engagement.store.add_host(row["host"], row["note"] or "")
            after = {h["host"] for h in engagement.store.list_hosts()}
            stats.hosts = len(after - before)

        if "services" in tables:
            for row in conn.execute(
                "SELECT host, port, proto, service, version, note FROM services"
            ):
                host = row["host"]
                port = row["port"]
                proto = row["proto"] or "tcp"
                if engagement.store.service_exists(host, port, proto):
                    continue
                engagement.store.add_service(
                    host,
                    port=port,
                    proto=proto,
                    service=row["service"] or "",
                    version=row["version"] or "",
                    note=row["note"] or "",
                )
                stats.services += 1

        if "findings" in tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(findings)")}
            select = (
                "SELECT title, severity, host, evidence, recommendation, stage, "
                "cvss, tags, notes"
            )
            if "confidence" in cols:
                select += ", confidence, verification_method"
            select += " FROM findings"
            for row in conn.execute(select):
                title = row["title"] or ""
                severity = row["severity"] or "info"
                host = row["host"] or ""
                evidence = row["evidence"] or ""
                if engagement.store.find_finding_id(title, host, severity, evidence) is not None:
                    stats.skipped_findings += 1
                    continue
                kwargs: dict = {
                    "title": title,
                    "severity": severity,
                    "host": host,
                    "evidence": evidence,
                    "recommendation": row["recommendation"] or "",
                    "stage": row["stage"] or "",
                    "cvss": row["cvss"] or "",
                    "tags": row["tags"] or "",
                    "notes": row["notes"] or "",
                }
                if "confidence" in cols:
                    kwargs["confidence"] = row["confidence"]
                    kwargs["verification_method"] = row["verification_method"] or ""
                engagement.store.add_finding(**kwargs)
                stats.findings += 1
    finally:
        conn.close()

    engagement.store.log_activity(
        "merge",
        f"from {path.name}: {stats.summary()}",
    )
    return stats
