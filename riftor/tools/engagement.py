"""Engagement tools: the agent drives RIFT stage + records findings/services."""

from __future__ import annotations

from riftor.engagement.cvss import base_score, severity_from_score
from riftor.engagement.parsers import SUPPORTED, parse
from riftor.engagement.report import write_reports
from riftor.tools.base import Tool, ToolContext, ToolResult

_SEVERITIES = ["info", "low", "medium", "high", "critical"]


class SetStageTool(Tool):
    name = "set_stage"
    description = (
        "Set the RIFT engagement stage as you progress: R=Recon, I=Intrusion, "
        "F=Foothold, T=Takeover. Call this when you move between stages."
    )
    parameters = {
        "type": "object",
        "properties": {"stage": {"type": "string", "enum": ["R", "I", "F", "T"]}},
        "required": ["stage"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        if eng.set_stage(str(args.get("stage", ""))):
            return ToolResult(f"stage set to {eng.stage}")
        return ToolResult("error: stage must be one of R/I/F/T", is_error=True)


class ScopeListTool(Tool):
    name = "scope_list"
    description = (
        "List the in-scope and out-of-scope targets. ALWAYS check this before "
        "touching any target so you stay in scope."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        in_scope = ", ".join(t.raw for t in eng.scope.in_scope) or "(none)"
        out_scope = ", ".join(t.raw for t in eng.scope.out_of_scope) or "(none)"
        enforce = "on" if eng.enforce else "off"
        return ToolResult(
            f"enforcement: {enforce}\nin-scope: {in_scope}\nout-of-scope: {out_scope}"
        )


class RecordServiceTool(Tool):
    name = "record_service"
    description = "Record a discovered host/port/service in the engagement state."
    parameters = {
        "type": "object",
        "properties": {
            "host": {"type": "string"},
            "port": {"type": "integer"},
            "proto": {"type": "string", "description": "tcp/udp (default tcp)"},
            "service": {"type": "string"},
            "version": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["host"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        eng.add_service(
            host=str(args["host"]),
            port=args.get("port"),
            proto=str(args.get("proto") or "tcp"),
            service=str(args.get("service") or ""),
            version=str(args.get("version") or ""),
            note=str(args.get("note") or ""),
        )
        where = f"{args['host']}:{args.get('port', '')}".rstrip(":")
        return ToolResult(f"recorded service on {where}")


class RecordFindingTool(Tool):
    name = "record_finding"
    description = (
        "Record a security finding (vulnerability/weakness) in the engagement: "
        "title, severity, affected host, evidence, and a remediation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "severity": {"type": "string", "enum": _SEVERITIES},
            "host": {"type": "string"},
            "evidence": {"type": "string"},
            "recommendation": {"type": "string"},
            "cvss_vector": {
                "type": "string",
                "description": "Optional CVSS v3.1 vector, e.g. "
                "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H. If given, severity is "
                "derived from the computed score.",
            },
        },
        "required": ["title", "severity"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        severity = str(args.get("severity", "info")).lower()
        if severity not in _SEVERITIES:
            severity = "info"

        vector = str(args.get("cvss_vector") or "").strip()
        score = base_score(vector) if vector else None
        if score is not None:
            severity = severity_from_score(score)
        else:
            vector = ""  # don't store an invalid vector

        fid = eng.add_finding(
            title=str(args["title"]),
            severity=severity,
            host=str(args.get("host") or ""),
            evidence=str(args.get("evidence") or ""),
            recommendation=str(args.get("recommendation") or ""),
            cvss=vector,
        )
        tag = severity + (f" · CVSS {score:.1f}" if score is not None else "")
        return ToolResult(f"recorded finding #{fid} [{tag}] {args['title']}")


class ImportScanTool(Tool):
    name = "import_scan"
    description = (
        "Parse the raw output of a recon tool and record the discovered "
        "services/findings into the engagement. Run the scan with bash, then "
        "pass its output here instead of recording each result by hand. "
        f"Supported tools: {', '.join(SUPPORTED)}. Handles normal and JSON output."
    )
    parameters = {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "enum": list(SUPPORTED)},
            "output": {"type": "string", "description": "Raw stdout from the tool."},
        },
        "required": ["tool", "output"],
    }

    def preview(self, args: dict) -> str:
        return f"{args.get('tool', '?')} ({len(str(args.get('output', '')))} bytes)"

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        tool = str(args.get("tool", "")).lower()
        if tool not in SUPPORTED:
            return ToolResult(f"error: tool must be one of {', '.join(SUPPORTED)}", is_error=True)
        scan = parse(tool, str(args.get("output", "")))
        for service in scan.services:
            eng.add_service(**service)
        for finding in scan.findings:
            eng.add_finding(**finding)
        if not scan.services and not scan.findings:
            return ToolResult(f"parsed {tool}: nothing recognised (check the output format)")
        return ToolResult(
            f"imported {tool}: {len(scan.services)} service(s), {len(scan.findings)} finding(s)"
        )


class GenerateReportTool(Tool):
    name = "generate_report"
    description = "Write a pentest report of the current engagement to disk (markdown + HTML)."
    parameters = {
        "type": "object",
        "properties": {
            "format": {"type": "string", "enum": ["md", "html", "both"]},
        },
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        fmt = str(args.get("format") or "both").lower()
        if fmt not in ("md", "html", "both"):
            fmt = "both"
        paths = write_reports(eng, fmt)
        return ToolResult("wrote report:\n" + "\n".join(str(p) for p in paths))
