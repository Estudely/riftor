"""Render the engagement into a pentest report (markdown, HTML, JSON, SARIF)."""

from __future__ import annotations

import html
import json
import time
from pathlib import Path

from riftor.engagement.cvss import base_score

_SEV_RANK = {"critical": 5, "high": 4, "medium": 3, "low": 2, "info": 1}
_SEV_ORDER = ["critical", "high", "medium", "low", "info"]
_STAGE_NAMES = {"R": "Recon", "I": "Intrusion", "F": "Foothold", "T": "Takeover"}
# SARIF maps severity onto its small enum; "warning" covers the middle band.
_SARIF_LEVEL = {
    "critical": "error", "high": "error", "medium": "warning",
    "low": "note", "info": "note",
}
_FORMATS = ("md", "html", "json", "sarif", "both", "all")


def report_data(engagement) -> dict:
    store = engagement.store
    findings = store.list_findings()
    for f in findings:
        vector = f.get("cvss") or ""
        f["cvss_score"] = base_score(vector) if vector else None
    findings.sort(
        key=lambda f: (
            -_SEV_RANK.get(f.get("severity", "info"), 0),
            -(f.get("cvss_score") or 0.0),
            f["id"],
        )
    )
    counts = {sev: 0 for sev in _SEV_ORDER}
    for f in findings:
        counts[f.get("severity", "info")] = counts.get(f.get("severity", "info"), 0) + 1

    from riftor.engagement.graph import build_graph, to_mermaid

    graph = build_graph(engagement)
    attack_graph = to_mermaid(graph) if graph["nodes"] else ""

    workdir = engagement.dir.parent
    return {
        "title": store.get_meta("name") or workdir.name or "engagement",
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": engagement.stage,
        "stage_name": _STAGE_NAMES.get(engagement.stage, engagement.stage),
        "scope_in": [t.raw for t in engagement.scope.in_scope],
        "scope_out": [t.raw for t in engagement.scope.out_of_scope],
        "counts": counts,
        "exec_summary": _exec_summary(counts, findings),
        "findings": findings,
        "services": store.list_services(),
        "hosts": store.list_hosts(),
        "attack_graph": attack_graph,
    }


def _exec_summary(counts: dict, findings: list[dict]) -> str:
    """A short, auto-generated business-impact paragraph for stakeholders."""
    total = len(findings)
    if not total:
        return "No findings were recorded during this engagement."
    crit, high = counts.get("critical", 0), counts.get("high", 0)
    med, low = counts.get("medium", 0), counts.get("low", 0)
    if crit:
        risk = "critical"
        lede = (
            f"This engagement uncovered {crit} critical "
            f"{'issue' if crit == 1 else 'issues'} requiring immediate remediation."
        )
    elif high:
        risk = "high"
        lede = f"This engagement uncovered {high} high-severity {'issue' if high == 1 else 'issues'}."
    elif med:
        risk = "medium"
        lede = "This engagement surfaced moderate-risk issues worth scheduled remediation."
    else:
        risk = "low"
        lede = "This engagement surfaced only low-risk or informational items."
    top = findings[0]["title"] if findings else ""
    parts = [
        lede,
        f"Overall risk is assessed as **{risk}** across {total} "
        f"total {'finding' if total == 1 else 'findings'} "
        f"({crit} critical, {high} high, {med} medium, {low} low).",
    ]
    if crit or high:
        parts.append(
            f"Prioritise the highest-severity items first — beginning with “{top}”."
        )
    return " ".join(parts)


def _cvss_label(finding: dict) -> str:
    score = finding.get("cvss_score")
    if score is None:
        return ""
    return f"{score:.1f}"


def _host_to_uri(host: str) -> str:
    """Turn a bare host/IP into a valid SARIF artifactLocation URI (RFC 3986).

    A bare ``10.0.0.5`` or ``example.com`` is not a valid URI reference, which
    strict SARIF consumers (GitHub Code Scanning, Azure DevOps) reject. If the
    host already carries a scheme we keep it; otherwise we emit an authority-only
    URI (``//host``). IPv6 literals are bracketed.
    """
    host = host.strip()
    if not host:
        return ""
    if "://" in host:
        return host
    # IPv6 literal → bracket it per RFC 3986 §3.2.2
    if host.count(":") >= 2 and not host.startswith("["):
        host = f"[{host}]"
    return f"//{host}"


def _fence_block(text: str) -> str:
    """Render *text* as a fenced code block that untrusted content can't escape.

    Per CommonMark, a fenced block is closed only by a fence of *at least* as
    many backticks as the opening one. Scan the content for its longest backtick
    run and open with one more, so an evidence string containing ``` (from a
    hostile scan banner, issue #119) can't terminate the fence early and inject
    live markdown/HTML into the report.
    """
    longest = 0
    run = 0
    for ch in text:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


def _md_inline(text: str) -> str:
    """Neutralize markdown control chars in an inline field (issue #119).

    Untrusted scan-derived strings (titles, notes, recommendations) are rendered
    inline, not fenced, so escape the characters that would otherwise let a
    hostile value inject links, emphasis, or raw HTML. Backslash-escaping is the
    CommonMark-sanctioned way to render these literally.
    """
    out = []
    for ch in str(text):
        if ch in "\\`*_{}[]()#+-.!<>|~":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def build_markdown(data: dict) -> str:
    out: list[str] = []
    out.append(f"# {data['title']} — riftor report")
    out.append("")
    out.append(f"_Generated {data['generated']} · stage reached: {data['stage_name']}_")
    out.append("")

    out.append("## Executive summary")
    out.append("")
    out.append(data.get("exec_summary", ""))
    out.append("")

    out.append("## Scope")
    out.append(f"- **In scope:** {', '.join(data['scope_in']) or '(none)'}")
    if data["scope_out"]:
        out.append(f"- **Out of scope:** {', '.join(data['scope_out'])}")
    out.append("")

    out.append("## Summary")
    out.append("")
    out.append("| Severity | Count |")
    out.append("| --- | --- |")
    for sev in _SEV_ORDER:
        out.append(f"| {sev.capitalize()} | {data['counts'].get(sev, 0)} |")
    out.append(f"\n**Total findings:** {len(data['findings'])}")
    out.append("")

    if data.get("attack_graph"):
        out.append("## Attack graph")
        out.append("")
        out.append("```mermaid")
        out.append(data["attack_graph"].rstrip())
        out.append("```")
        out.append("")

    out.append("## Findings")
    out.append("")
    if not data["findings"]:
        out.append("_No findings recorded._")
    for idx, f in enumerate(data["findings"], 1):
        cvss = _cvss_label(f)
        tag = f"{f.get('severity', 'info').upper()}"
        if cvss:
            tag += f" · CVSS {cvss}"
        out.append(f"### {idx}. [{tag}] {_md_inline(f['title'])}")
        if f.get("host"):
            out.append(f"- **Host:** {_md_inline(f['host'])}")
        if f.get("tags"):
            out.append(f"- **Tags:** {_md_inline(f['tags'])}")
        if f.get("cvss"):
            out.append(f"- **CVSS:** {cvss} (`{_md_inline(f['cvss'])}`)")
        if f.get("evidence"):
            out.append(f"- **Evidence:**\n\n{_fence_block(str(f['evidence']))}")
        if f.get("recommendation"):
            out.append(f"- **Recommendation:** {_md_inline(f['recommendation'])}")
        if f.get("notes"):
            out.append(f"- **Notes:** {_md_inline(f['notes'])}")
        out.append("")

    if data["services"]:
        out.append("## Appendix — Hosts & Services")
        out.append("")
        out.append("| Host | Port | Proto | Service | Version |")
        out.append("| --- | --- | --- | --- | --- |")
        for s in data["services"]:
            out.append(
                f"| {s.get('host', '')} | {s.get('port') or ''} | {s.get('proto', '')} "
                f"| {s.get('service', '')} | {s.get('version', '')} |"
            )
        out.append("")

    out.append("---")
    out.append("_RIFT methodology · generated by riftor_")
    return "\n".join(out) + "\n"


_HTML_CSS = """
:root { color-scheme: dark; }
body { background:#0a0a12; color:#c8c8d4; font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;
       max-width:900px; margin:0 auto; padding:2rem; }
h1,h2,h3 { color:#e9e9f2; }
h1 { border-bottom:2px solid #a855f7; padding-bottom:.3rem; }
h2 { border-bottom:1px solid #2a2a3a; padding-bottom:.2rem; margin-top:2rem; }
a { color:#22d3ee; }
code,pre { background:#12121c; border-radius:4px; }
code { padding:.1rem .3rem; }
pre { padding:.8rem; overflow:auto; border-left:3px solid #22d3ee; }
table { border-collapse:collapse; width:100%; margin:.5rem 0; }
th,td { border:1px solid #2a2a3a; padding:.4rem .6rem; text-align:left; }
th { background:#14141f; color:#a855f7; }
.muted { color:#8b8ba7; }
.finding { border-left:4px solid #2a2a3a; padding:.2rem 1rem; margin:1rem 0; background:#101019; }
.sev-critical { border-left-color:#e11d48; }
.sev-high { border-left-color:#f0abfc; }
.sev-medium { border-left-color:#a855f7; }
.sev-low { border-left-color:#22d3ee; }
.sev-info { border-left-color:#4a4a5a; }
.badge { font-weight:700; }
"""


def build_html(data: dict) -> str:
    def esc(value) -> str:
        return html.escape(str(value))

    parts: list[str] = []
    parts.append("<!doctype html><html lang=en><head><meta charset=utf-8>")
    parts.append(f"<title>{esc(data['title'])} — riftor report</title>")
    parts.append(f"<style>{_HTML_CSS}</style></head><body>")
    parts.append(f"<h1>{esc(data['title'])} <span class=muted>— riftor report</span></h1>")
    parts.append(
        f"<p class=muted>Generated {esc(data['generated'])} · stage reached: "
        f"{esc(data['stage_name'])}</p>"
    )

    parts.append("<h2>Executive summary</h2>")
    parts.append(f"<p>{esc(data.get('exec_summary', '')).replace('**', '')}</p>")

    parts.append("<h2>Scope</h2><ul>")
    parts.append(f"<li><b>In scope:</b> {esc(', '.join(data['scope_in']) or '(none)')}</li>")
    if data["scope_out"]:
        parts.append(f"<li><b>Out of scope:</b> {esc(', '.join(data['scope_out']))}</li>")
    parts.append("</ul>")

    parts.append("<h2>Summary</h2><table><tr><th>Severity</th><th>Count</th></tr>")
    for sev in _SEV_ORDER:
        parts.append(f"<tr><td>{sev.capitalize()}</td><td>{data['counts'].get(sev, 0)}</td></tr>")
    parts.append("</table>")
    parts.append(f"<p><b>Total findings:</b> {len(data['findings'])}</p>")

    parts.append("<h2>Findings</h2>")
    if not data["findings"]:
        parts.append("<p class=muted>No findings recorded.</p>")
    for idx, f in enumerate(data["findings"], 1):
        sev = f.get("severity", "info")
        cvss = _cvss_label(f)
        tag = sev.upper() + (f" · CVSS {cvss}" if cvss else "")
        parts.append(f"<div class='finding sev-{esc(sev)}'>")
        parts.append(f"<h3>{idx}. <span class=badge>[{esc(tag)}]</span> {esc(f['title'])}</h3>")
        if f.get("host"):
            parts.append(f"<p><b>Host:</b> {esc(f['host'])}</p>")
        if f.get("tags"):
            parts.append(f"<p><b>Tags:</b> {esc(f['tags'])}</p>")
        if f.get("cvss"):
            parts.append(f"<p><b>CVSS:</b> {esc(cvss)} (<code>{esc(f['cvss'])}</code>)</p>")
        if f.get("evidence"):
            parts.append(f"<p><b>Evidence:</b></p><pre>{esc(f['evidence'])}</pre>")
        if f.get("recommendation"):
            parts.append(f"<p><b>Recommendation:</b> {esc(f['recommendation'])}</p>")
        if f.get("notes"):
            parts.append(f"<p><b>Notes:</b> {esc(f['notes'])}</p>")
        parts.append("</div>")

    if data["services"]:
        parts.append("<h2>Appendix — Hosts &amp; Services</h2>")
        parts.append("<table><tr><th>Host</th><th>Port</th><th>Proto</th><th>Service</th><th>Version</th></tr>")
        for s in data["services"]:
            parts.append(
                f"<tr><td>{esc(s.get('host', ''))}</td><td>{esc(s.get('port') or '')}</td>"
                f"<td>{esc(s.get('proto', ''))}</td><td>{esc(s.get('service', ''))}</td>"
                f"<td>{esc(s.get('version', ''))}</td></tr>"
            )
        parts.append("</table>")

    parts.append("<hr><p class=muted>RIFT methodology · generated by riftor</p>")
    parts.append("</body></html>")
    return "".join(parts)


def build_json(data: dict) -> str:
    """Machine-readable report: the full structured engagement data."""
    payload = {
        "tool": "riftor",
        "title": data["title"],
        "generated": data["generated"],
        "stage": data["stage"],
        "scope": {"in": data["scope_in"], "out": data["scope_out"]},
        "summary": {"counts": data["counts"], "total": len(data["findings"])},
        "executive_summary": data.get("exec_summary", ""),
        "findings": [
            {
                "id": f.get("id"),
                "title": f.get("title"),
                "severity": f.get("severity"),
                "cvss_vector": f.get("cvss") or None,
                "cvss_score": f.get("cvss_score"),
                "host": f.get("host") or None,
                "evidence": f.get("evidence") or None,
                "recommendation": f.get("recommendation") or None,
                "tags": f.get("tags") or None,
                "notes": f.get("notes") or None,
                "stage": f.get("stage") or None,
            }
            for f in data["findings"]
        ],
        "services": data["services"],
        "hosts": data["hosts"],
    }
    return json.dumps(payload, indent=2)


def build_sarif(data: dict) -> str:
    """SARIF v2.1.0 — for GitHub Advanced Security, defect trackers, etc."""
    # Qualitative severity → a synthetic CVSS-style score, used only when a
    # finding has no CVSS vector so GitHub's security-severity classification
    # doesn't collapse a manually-rated "critical" to 0.0/low.
    _SEV_SCORE = {"critical": 9.5, "high": 7.5, "medium": 5.0, "low": 2.5, "info": 0.0}
    rules: dict[str, dict] = {}
    results: list[dict] = []
    for f in data["findings"]:
        rule_id = (f.get("title") or "finding").strip() or "finding"
        # security-severity: prefer the real CVSS score; else derive from the
        # qualitative severity. Track the MAX across findings sharing a rule so a
        # later low-scored finding can't clobber an earlier high one.
        score = f.get("cvss_score")
        if score is None:
            score = _SEV_SCORE.get(f.get("severity", "info"), 0.0)
        if rule_id in rules:
            prev = float(rules[rule_id]["properties"]["security-severity"])
            if score > prev:
                rules[rule_id]["properties"]["security-severity"] = f"{score:.1f}"
        else:
            rules[rule_id] = {
                "id": rule_id,
                "name": rule_id,
                "shortDescription": {"text": rule_id},
                "properties": {"security-severity": f"{score:.1f}"},
            }
        message = f.get("evidence") or f.get("recommendation") or rule_id
        result = {
            "ruleId": rule_id,
            "level": _SARIF_LEVEL.get(f.get("severity", "info"), "note"),
            "message": {"text": str(message)},
            "properties": {
                "severity": f.get("severity"),
                "host": f.get("host") or "",
                "tags": f.get("tags") or "",
            },
        }
        host = f.get("host")
        if host:
            # SARIF artifactLocation.uri must be a valid URI reference (RFC 3986).
            # A bare host/IP isn't one, so encode it as an authority-only URI.
            uri = _host_to_uri(str(host))
            result["locations"] = [
                {"physicalLocation": {"artifactLocation": {"uri": uri}}}
            ]
        results.append(result)

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "riftor",
                        "informationUri": "https://github.com/Estudely/riftor",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)


_BUILDERS = {
    "md": ("md", build_markdown),
    "html": ("html", build_html),
    "json": ("json", build_json),
    "sarif": ("sarif", build_sarif),
}


def _formats_for(fmt: str) -> list[str]:
    fmt = (fmt or "both").lower()
    if fmt == "both":
        return ["md", "html"]
    if fmt == "all":
        return ["md", "html", "json", "sarif"]
    return [fmt] if fmt in _BUILDERS else []


def write_reports(engagement, fmt: str = "both") -> list[Path]:
    """Render and write report file(s). fmt in {md, html, json, sarif, both, all}."""
    formats = _formats_for(fmt)
    if not formats:
        raise ValueError(f"unknown report format: {fmt}")
    data = report_data(engagement)
    out_dir = engagement.dir / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    written: list[Path] = []
    for key in formats:
        ext, builder = _BUILDERS[key]
        path = out_dir / f"report-{stamp}.{ext}"
        path.write_text(builder(data), encoding="utf-8")
        written.append(path)
    return written
