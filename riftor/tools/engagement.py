"""Engagement tools: the agent drives RIFT stage + records findings/services."""

from __future__ import annotations

from pathlib import Path

from riftor.engagement.cvss import base_score, severity_from_score
from riftor.engagement.parsers import SUPPORTED, parse
from riftor.engagement.report import write_reports
from riftor.tools.base import Tool, ToolContext, ToolResult

_SEVERITIES = ["info", "low", "medium", "high", "critical"]


def _parse_confidence(value: object) -> int | None:
    """Coerce a confidence arg to an int clamped to 0-10, or None if unset/invalid.

    None (rather than 0) keeps the column NULL so callers can tell "not scored"
    apart from "scored zero".
    """
    if value is None or value == "":
        return None
    try:
        return max(0, min(10, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


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
            "confidence": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10,
                "description": "How sure you are (0-10). 8+ requires a complete "
                "source→sink chain AND an attacker model; otherwise cap at 6.",
            },
            "verification_method": {
                "type": "string",
                "description": "How the finding was confirmed, e.g. 'OOB callback', "
                "'reflected canary', 'timing delta'. Status codes alone are not proof.",
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

        confidence = _parse_confidence(args.get("confidence"))

        title = str(args["title"])
        host = str(args.get("host") or "")
        fid, action = eng.add_finding_dedup(
            dedup="skip",
            title=title,
            severity=severity,
            host=host,
            evidence=str(args.get("evidence") or ""),
            recommendation=str(args.get("recommendation") or ""),
            tags=str(args.get("tags") or ""),
            notes=str(args.get("notes") or ""),
            cvss=vector,
            confidence=confidence,
            verification_method=str(args.get("verification_method") or ""),
        )
        if action == "skipped":
            return ToolResult(f"finding already recorded (#{fid}) — skipped duplicate")
        # Check for fuzzy duplicates across tools.
        similar = ctx.engagement.store.find_similar(title, host) if ctx.engagement else []
        tag_line = severity + (f" · CVSS {score:.1f}" if score is not None else "")
        msg = f"recorded finding #{fid} [{tag_line}] {title}"
        if similar:
            ids = ", ".join(f"#{s['id']}" for s in similar[:3])
            msg += f"  ⚠ similar to: {ids} — review and /edit-finding or /delete-finding to merge"
        return ToolResult(msg)


class EditFindingTool(Tool):
    name = "edit_finding"
    description = (
        "Update an existing finding by id (from /findings). Pass only the fields to "
        "change: title, severity, host, evidence, recommendation, tags, notes, "
        "confidence, verification_method, cvss_vector. "
        "When cvss_vector is given, the severity is auto-derived from the computed "
        "CVSS v3.1 base score — do NOT also pass severity in that case."
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
            "confidence": {"type": "integer", "minimum": 0, "maximum": 10},
            "verification_method": {"type": "string"},
            "cvss_vector": {
                "type": "string",
                "description": "CVSS v3.1 vector, e.g. "
                "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H. Severity is "
                "auto-derived from the computed score.",
            },
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
        fields: dict = {
            k: str(args[k])
            for k in ("title", "severity", "host", "evidence", "recommendation",
                      "tags", "notes", "verification_method")
            if k in args and args[k] is not None
        }
        if args.get("confidence") is not None:
            fields["confidence"] = _parse_confidence(args.get("confidence"))
        # When cvss_vector is given, auto-derive severity from the computed score.
        vector = str(args.get("cvss_vector") or "").strip()
        if vector:
            score = base_score(vector)
            if score is not None:
                fields["severity"] = severity_from_score(score)
                fields["cvss"] = vector
        if "severity" in fields and fields["severity"].lower() not in _SEVERITIES:
            return ToolResult(f"error: severity must be one of {', '.join(_SEVERITIES)}", is_error=True)
        if not eng.store.update_finding(fid, **fields):
            return ToolResult(f"error: no finding #{fid}", is_error=True)
        eng.store.log_activity("finding_edit", f"#{fid} {', '.join(fields)}")
        note = ""
        if vector and score is not None:
            note = f" · CVSS {score:.1f} ({severity_from_score(score)})"
        return ToolResult(f"updated finding #{fid} ({', '.join(fields) or 'no changes'}{note})")


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


class LoadSkillTool(Tool):
    name = "load_skill"
    description = (
        "Load an operator-provided methodology skill for a domain, if one exists. "
        "Skills carry checklists, payloads, tool commands, and evidence standards "
        "(e.g. recon, exploitation, payloads, reporting). When a matching skill is "
        "available, prefer it; if none is found this returns the list of available "
        "skills (which may be empty) — just proceed with your own judgment."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name (e.g. 'recon', 'exploitation', 'payloads', 'reporting')",
            },
        },
        "required": ["name"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        import os
        skill_name = str(args.get("name", "")).strip().lower()
        if not skill_name:
            return ToolResult("error: skill name is required", is_error=True)
        if not skill_name.endswith(".md"):
            skill_name += ".md"

        # Search in XDG config skills dir, then engagement dir
        search_paths = []
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        search_paths.append(Path(base) / "riftor" / "skills" / skill_name)
        if ctx.engagement:
            search_paths.append(ctx.engagement.dir / "skills" / skill_name)

        for p in search_paths:
            if p.exists():
                try:
                    content = p.read_text()[:24000]
                    return ToolResult(f"# SKILL: {skill_name}\n\n{content}")
                except OSError as e:
                    return ToolResult(f"error reading skill: {e}", is_error=True)

        available = []
        for sp in search_paths:
            if sp.parent.exists():
                available.extend(f.stem for f in sp.parent.glob("*.md"))
        avail_str = ", ".join(sorted(set(available))) if available else "(none found)"
        return ToolResult(
            f"skill '{skill_name}' not found. Available: {avail_str}. "
            f"Searched: {', '.join(str(p.parent) for p in search_paths)}"
        )


class RecordHypothesisTool(Tool):
    name = "record_hypothesis"
    description = (
        "Record a hypothesis — something you suspect but haven't confirmed yet. "
        "Track open leads so you never forget to test them and never re-test refuted ones."
    )
    parameters = {
        "type": "object",
        "properties": {
            "statement": {"type": "string", "description": "What you suspect, e.g. 'SSRF in /webhook can reach internal metadata'"},
            "rationale": {"type": "string", "description": "Why you suspect this (evidence so far)"},
        },
        "required": ["statement"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        statement = str(args.get("statement", "")).strip()
        if not statement:
            return ToolResult("error: statement is required", is_error=True)
        rationale = str(args.get("rationale") or "")
        hid = eng.store.add_hypothesis(statement, rationale=rationale)
        return ToolResult(f"hypothesis #{hid} recorded [open]: {statement}")


class ResolveHypothesisTool(Tool):
    name = "resolve_hypothesis"
    description = (
        "Resolve a hypothesis: confirmed (evidence proves it), refuted (evidence disproves it), "
        "or inconclusive. Never re-test a refuted hypothesis."
    )
    parameters = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "Hypothesis id (from list_hypotheses)"},
            "status": {"type": "string", "enum": ["confirmed", "refuted", "inconclusive"]},
            "rationale": {"type": "string", "description": "Why this resolution (evidence)"},
        },
        "required": ["id", "status"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        try:
            hid = int(args["id"])
        except (KeyError, TypeError, ValueError):
            return ToolResult("error: id must be an integer", is_error=True)
        status = str(args.get("status", "")).lower()
        rationale = str(args.get("rationale") or "")
        if eng.store.resolve_hypothesis(hid, status, rationale):
            return ToolResult(f"hypothesis #{hid} → {status}")
        return ToolResult(f"error: no hypothesis #{hid} or invalid status", is_error=True)


class ListHypothesesTool(Tool):
    name = "list_hypotheses"
    description = "List hypotheses (open leads, confirmed, refuted). Check before testing to avoid re-work."
    parameters = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["open", "confirmed", "refuted", "inconclusive", "all"],
                       "description": "Filter by status (default: all)"},
        },
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        eng = ctx.engagement
        if eng is None:
            return ToolResult("error: no active engagement", is_error=True)
        status = str(args.get("status") or "").lower()
        rows = eng.store.list_hypotheses(status if status and status != "all" else None)
        if not rows:
            return ToolResult("no hypotheses recorded yet")
        lines = []
        for r in rows:
            lines.append(f"#{r['id']} [{r['status']}] {r['statement']}")
            if r.get("rationale"):
                lines.append(f"   rationale: {r['rationale'][:150]}")
        return ToolResult("\n".join(lines))


class RecordLessonTool(Tool):
    name = "record_lesson"
    description = (
        "Save a durable lesson that persists across sessions. Use after learning "
        "something important — a correction from the operator, a pattern that worked, "
        "a mistake to avoid. Format: WHEN <trigger> → <what to do>."
    )
    parameters = {
        "type": "object",
        "properties": {
            "trigger": {
                "type": "string",
                "description": "When this lesson applies, e.g. 'testing JWT' or 'scanning ports'.",
            },
            "lesson": {
                "type": "string",
                "description": "What to do or avoid, e.g. 'always check alg=none first'.",
            },
            "source": {
                "type": "string",
                "enum": ["operator", "agent"],
                "description": "Who taught it (default: operator).",
            },
        },
        "required": ["lesson"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        from riftor.engagement.lessons import LessonStore
        trigger = str(args.get("trigger") or "").strip()
        lesson_text = str(args.get("lesson") or "").strip()
        source = str(args.get("source") or "operator")
        if not lesson_text:
            return ToolResult("error: lesson text is required", is_error=True)
        try:
            store = LessonStore()
            entry = store.add(trigger, lesson_text, source)
            display = f"WHEN {entry.trigger} → {entry.lesson}" if entry.trigger else entry.lesson
            return ToolResult(f"lesson saved (#{entry.id}): {display}")
        except Exception as e:
            return ToolResult(f"error saving lesson: {e}", is_error=True)


class ListLessonsTool(Tool):
    name = "list_lessons"
    description = "List all saved lessons (cross-session memory)."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        from riftor.engagement.lessons import LessonStore
        store = LessonStore()
        rows = store.list()
        if not rows:
            return ToolResult("no lessons saved yet")
        lines = []
        for r in rows:
            trigger = r.get("trigger", "")
            lesson = r.get("lesson", "")
            source = r.get("source", "operator")
            lid = r.get("id", "?")
            if trigger:
                lines.append(f"#{lid} [{source}] WHEN {trigger} → {lesson}")
            else:
                lines.append(f"#{lid} [{source}] {lesson}")
        return ToolResult("\n".join(lines))


class RememberTool(Tool):
    name = "remember"
    description = (
        "Store a durable fact, preference, or decision for THIS engagement. It "
        "persists across sessions and is recalled automatically. Use for target "
        "quirks, where creds live, operator preferences, or decisions you made."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The fact to remember."},
            "tag": {
                "type": "string",
                "description": "Optional short category, e.g. 'creds' or 'pref'.",
            },
        },
        "required": ["text"],
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        from riftor.engagement.memory import MemoryStore
        text = str(args.get("text") or "").strip()
        tag = str(args.get("tag") or "").strip()
        if not text:
            return ToolResult("error: text is required", is_error=True)
        try:
            entry = MemoryStore(ctx.workdir).add(text, tag, source="agent")
            label = f"[{entry.tag}] {entry.text}" if entry.tag else entry.text
            return ToolResult(f"remembered (#{entry.id}): {label}")
        except Exception as e:
            return ToolResult(f"error saving memory: {e}", is_error=True)
