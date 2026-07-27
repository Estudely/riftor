"""Attack graph from engagement state → Mermaid flowchart."""

from __future__ import annotations

import re
from pathlib import Path

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _sanitize_id(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "node"


def _short(text: str, limit: int = 40) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def build_graph(engagement) -> dict:
    """Build nodes/edges from hosts, services, and findings by severity."""
    store = engagement.store
    nodes: list[dict] = []
    edges: list[dict] = []
    seen: set[str] = set()

    def add_node(node_id: str, label: str, kind: str, severity: str | None = None) -> str:
        sid = _sanitize_id(node_id)
        if sid not in seen:
            entry: dict = {"id": sid, "label": _short(label), "kind": kind}
            if severity:
                entry["severity"] = severity
            nodes.append(entry)
            seen.add(sid)
        return sid

    for h in store.list_hosts():
        host = h.get("host", "")
        if host:
            add_node(f"host_{host}", host, "host")

    for s in store.list_services():
        host = (s.get("host") or "").strip()
        if not host:
            continue
        hid = add_node(f"host_{host}", host, "host")
        port = s.get("port")
        svc_name = s.get("service") or ""
        label = f"{port}/{svc_name}" if port else svc_name or "service"
        sid = add_node(f"svc_{s['id']}", label, "service")
        edges.append({"src": hid, "dst": sid, "kind": "hosts"})

    findings = sorted(
        store.list_findings(),
        key=lambda f: (_SEV_ORDER.get(f.get("severity", "info"), 9), f["id"]),
    )
    for f in findings:
        sev = (f.get("severity") or "info").lower()
        fid = add_node(
            f"find_{f['id']}",
            f"{f.get('title', 'finding')} ({sev})",
            "finding",
            severity=sev,
        )
        host = (f.get("host") or "").strip()
        if host:
            hid = add_node(f"host_{host}", host, "host")
            edges.append({"src": hid, "dst": fid, "kind": "on_host"})

    return {"nodes": nodes, "edges": edges}


def to_mermaid(graph: dict) -> str:
    """Render graph as a Mermaid flowchart TD diagram."""
    lines = ["flowchart TD"]
    for node in graph.get("nodes", []):
        nid = _sanitize_id(node["id"])
        label = node.get("label", nid).replace('"', "'")
        lines.append(f'  {nid}["{label}"]')
    for edge in graph.get("edges", []):
        src = _sanitize_id(edge["src"])
        dst = _sanitize_id(edge["dst"])
        lines.append(f"  {src} --> {dst}")
    return "\n".join(lines) + "\n"


def write_graph(engagement, out_dir: Path | None = None) -> Path:
    """Write `.riftor/reports/graph.mmd` (or *out_dir*/graph.mmd)."""
    graph = build_graph(engagement)
    mermaid = to_mermaid(graph)
    target_dir = out_dir or (engagement.dir / "reports")
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / "graph.mmd"
    path.write_text(mermaid, encoding="utf-8")
    return path
