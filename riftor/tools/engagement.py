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


class AddScopeTool(Tool):
    name = "add_scope"
    requires_permission = True
    description = (
        "Request adding one or more targets to the IN-SCOPE list so you can test "
        "them (e.g. a subdomain discovered on an in-scope host). Requires operator "
        "approval. This only WIDENS scope — you cannot remove or exclude targets. "
        "Give a clear reason so the operator can decide."
    )
    parameters = {
        "type": "object",
        "properties": {
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Targets to add: IP, CIDR, domain, or *.wildcard.",
            },
            "reason": {
                "type": "string",
                "description": "Why these belong in scope (shown to the operator).",
            },
        },
        "required": ["targets", "reason"],
    }

    def preview(self, args: dict) -> str:
        targets = args.get("targets") or []
        if isinstance(targets, str):
            targets = [targets]
        joined = ", ".join(str(t) for t in targets) or "(none)"
        reason = str(args.get("reason") or "").strip()
        text = f"add to scope: {joined}"
        if reason:
            text += f' — "{reason}"'
        return text[:300]

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        raw_targets = args.get("targets")
        if isinstance(raw_targets, str):
            raw_targets = [raw_targets]
        targets = [str(t).strip() for t in (raw_targets or []) if str(t).strip()]
        if not targets:
            return ToolResult("error: no targets given", is_error=True)

        existing = {t.raw for t in eng.scope.in_scope}
        added: list[str] = []
        already: list[str] = []
        for raw in targets:
            target = eng.add_scope(raw, "in")  # parse + persist + log
            if target.raw in existing:
                already.append(target.raw)
            else:
                existing.add(target.raw)
                added.append(target.raw)

        if not added and already:
            return ToolResult(f"already in scope: {', '.join(already)}")
        msg = f"added {len(added)} target(s) to scope: {', '.join(added)}"
        if already:
            msg += f" · {len(already)} already present"
        return ToolResult(msg)


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
            "tags": {
                "type": "string",
                "description": "Optional comma-separated tags, e.g. "
                "'requires-client-approval, pending-validation'.",
            },
            "notes": {"type": "string", "description": "Optional free-form markdown notes."},
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

        fid, action = eng.add_finding_dedup(
            dedup="skip",
            title=str(args["title"]),
            severity=severity,
            host=str(args.get("host") or ""),
            evidence=str(args.get("evidence") or ""),
            recommendation=str(args.get("recommendation") or ""),
            tags=str(args.get("tags") or ""),
            notes=str(args.get("notes") or ""),
            cvss=vector,
        )
        if action == "skipped":
            return ToolResult(f"finding already recorded (#{fid}) — skipped duplicate")
        tag = severity + (f" · CVSS {score:.1f}" if score is not None else "")
        return ToolResult(f"recorded finding #{fid} [{tag}] {args['title']}")


class EditFindingTool(Tool):
    name = "edit_finding"
    description = (
        "Update an existing finding by id (from /findings). Pass only the fields to "
        "change: title, severity, host, evidence, recommendation, tags, notes."
    )
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "Finding id to edit."},
            "title": {"type": "string"},
            "severity": {"type": "string", "enum": _SEVERITIES},
            "host": {"type": "string"},
            "evidence": {"type": "string"},
            "recommendation": {"type": "string"},
            "tags": {"type": "string"},
            "notes": {"type": "string"},
        },
        "required": ["id"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        try:
            fid = int(args["id"])
        except (KeyError, TypeError, ValueError):
            return ToolResult("error: id must be an integer", is_error=True)
        fields = {
            k: str(args[k])
            for k in ("title", "severity", "host", "evidence", "recommendation", "tags", "notes")
            if k in args and args[k] is not None
        }
        if "severity" in fields and fields["severity"].lower() not in _SEVERITIES:
            return ToolResult(f"error: severity must be one of {', '.join(_SEVERITIES)}", is_error=True)
        if not eng.store.update_finding(fid, **fields):
            return ToolResult(f"error: no finding #{fid}", is_error=True)
        eng.store.log_activity("finding_edit", f"#{fid} {', '.join(fields)}")
        return ToolResult(f"updated finding #{fid} ({', '.join(fields) or 'no changes'})")


class DeleteFindingTool(Tool):
    name = "delete_finding"
    description = "Delete a finding by id (from /findings). Use to remove duplicates or false positives."
    parameters = {
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "Finding id to delete."}},
        "required": ["id"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        try:
            fid = int(args["id"])
        except (KeyError, TypeError, ValueError):
            return ToolResult("error: id must be an integer", is_error=True)
        if not eng.store.delete_finding(fid):
            return ToolResult(f"error: no finding #{fid}", is_error=True)
        eng.store.log_activity("finding_delete", f"#{fid}")
        return ToolResult(f"deleted finding #{fid}")


class ListHostsTool(Tool):
    name = "list_hosts"
    description = "List discovered hosts and services recorded in the engagement."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        services = eng.store.list_services()
        if not services:
            hosts = eng.store.list_hosts()
            if not hosts:
                return ToolResult("no hosts or services recorded yet")
            return ToolResult("hosts:\n" + "\n".join(h["host"] for h in hosts))
        lines = [
            f"{s['host']}:{s.get('port') or ''}/{s.get('proto', 'tcp')} "
            f"{s.get('service', '')} {s.get('version', '')}".rstrip()
            for s in services
        ]
        return ToolResult("services:\n" + "\n".join(lines)).truncated()


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
            "dedup": {
                "type": "string",
                "enum": ["skip", "merge", "allow-all"],
                "description": "How to handle duplicates (default skip).",
            },
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
        dedup = str(args.get("dedup") or "skip").lower()
        if dedup not in ("skip", "merge", "allow-all"):
            dedup = "skip"
        scan = parse(tool, str(args.get("output", "")))

        svc_added = svc_skipped = 0
        for service in scan.services:
            _id, action = eng.add_service_dedup(dedup=dedup, **service)
            svc_added += action == "added"
            svc_skipped += action == "skipped"
        fnd_added = fnd_skipped = fnd_merged = 0
        for finding in scan.findings:
            _id, action = eng.add_finding_dedup(dedup=dedup, **finding)
            fnd_added += action == "added"
            fnd_skipped += action == "skipped"
            fnd_merged += action == "merged"

        if not scan.services and not scan.findings:
            extra = f" ({scan.skipped} line(s) skipped)" if scan.skipped else ""
            return ToolResult(f"parsed {tool}: nothing recognised{extra} (check the output format)")

        msg = f"imported {tool}: {svc_added} service(s), {fnd_added} finding(s)"
        details = []
        if svc_skipped or fnd_skipped:
            details.append(f"{svc_skipped + fnd_skipped} duplicate(s) skipped")
        if fnd_merged:
            details.append(f"{fnd_merged} merged")
        if scan.skipped:
            details.append(f"{scan.skipped} unparsable line(s)")
        if scan.json_errors:
            details.append(f"{scan.json_errors} JSON error(s)")
        if details:
            msg += " · " + ", ".join(details)
        return ToolResult(msg)


class GenerateReportTool(Tool):
    name = "generate_report"
    description = (
        "Write a pentest report of the current engagement to disk. Formats: md, html, "
        "json (machine-readable), sarif (CI/defect-tracker), both (md+html), all."
    )
    parameters = {
        "type": "object",
        "properties": {
            "format": {"type": "string", "enum": ["md", "html", "json", "sarif", "both", "all"]},
        },
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        fmt = str(args.get("format") or "both").lower()
        try:
            paths = write_reports(eng, fmt)
        except ValueError:
            paths = write_reports(eng, "both")
        eng.store.log_activity("report", fmt)
        return ToolResult("wrote report:\n" + "\n".join(str(p) for p in paths))
