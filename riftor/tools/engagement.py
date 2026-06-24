"""Engagement tools: the agent drives RIFT stage + records findings/services."""

from __future__ import annotations

import json
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


def _discover_skills(config, engagement) -> tuple[dict, list]:
    """Discover skills from built-in, config, and engagement roots.

    Returns (name_to_entry, all_entries) where each entry is
    (kebab_name, display_title, description, tags, subdomain, path).
    First root wins on name collisions.
    """
    import os
    from importlib import resources

    discovered: list[tuple[str, str, str, list[str], str, Path]] = []
    seen: set[str] = set()

    roots: list[Path] = []
    try:
        pkg_skills = resources.files("riftor.skills")
        if pkg_skills.is_dir() and (pkg_skills / "index.json").is_file():
            roots.append(Path(str(pkg_skills)))
    except Exception:
        pass
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    roots.append(Path(base) / "riftor" / "skills")
    if engagement:
        roots.append(engagement.dir / "skills")
    if config and config.skills_dir:
        roots.append(Path(config.skills_dir).expanduser())

    for root in roots:
        if not root.exists():
            continue
        if (root / "skills").is_dir():
            skills_subdir = root / "skills"
            index_path = root / "index.json"
        elif (root / "index.json").is_file() and any(root.glob("*/SKILL.md")):
            skills_subdir = root
            index_path = root / "index.json"
        else:
            skills_subdir = None
            index_path = None

        if skills_subdir and skills_subdir.is_dir():
            index_data = None
            if index_path and index_path.is_file():
                try:
                    index_data = json.loads(index_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            if index_data:
                entries = index_data.get("skills", [])
                for entry in entries:
                    kname = entry.get("name", "")
                    if kname in seen:
                        continue
                    desc = entry.get("description", "")
                    tags_list = entry.get("tags", [])
                    subd = entry.get("subdomain", "")
                    skill_path = root / entry.get("path", f"skills/{kname}")
                    # Resolve subdomain from SKILL.md if index lacks it
                    if not subd and skill_path.is_dir():
                        meta = _read_frontmatter(skill_path / "SKILL.md")
                        subd = meta.get("subdomain", "")
                    discovered.append((kname, kname, desc, tags_list, subd, skill_path))
                    seen.add(kname)
            else:
                for skill_md in sorted(skills_subdir.glob("*/SKILL.md")):
                    name_dir = skill_md.parent.name
                    if name_dir in seen:
                        continue
                    meta = _read_frontmatter(skill_md)
                    desc = meta.get("description", "").strip()
                    tags_list = meta.get("tags", [])
                    subd = meta.get("subdomain", "")
                    discovered.append(
                        (name_dir, meta.get("name", name_dir), desc, tags_list, subd, skill_md)
                    )
                    seen.add(name_dir)
        else:
            for mdfile in sorted(root.glob("*.md")):
                flat_name = mdfile.stem
                if flat_name in seen:
                    continue
                meta = _read_frontmatter(mdfile)
                title = meta.get("name", flat_name)
                desc = meta.get("description", "").strip()
                tags_list = meta.get("tags", [])
                subd = meta.get("subdomain", "")
                discovered.append((flat_name, title, desc, tags_list, subd, mdfile))
                seen.add(flat_name)

    return {e[0]: e for e in discovered}, discovered


class LoadSkillTool(Tool):
    name = "load_skill"
    description = (
        "Load a pentest methodology skill, if one is available. Skills carry "
        "step-by-step workflows, tool commands, prerequisites, and verification "
        "checklists written by practitioners. Call with `name` to search (e.g. "
        "'recon', 'pentest', 'sqli', 'kerberoasting', 'AD' — partial match OK). "
        "Call with `group` set to a domain (e.g. 'penetration-testing', "
        "'web-application-security', 'red-teaming') or `list` to browse the catalog. "
        "When a skill is found, prefer its workflow over working from memory. "
        "If nothing matches, just proceed with your own judgment."
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Skill name or keyword to search (partial match OK). Examples: "
                    "'recon', 'pentest', 'sqli', 'kerberoasting', 'volatility'. "
                    "Use 'list' or omit for a catalog overview."
                ),
            },
            "group": {
                "type": "string",
                "description": (
                    "Filter by domain/subdomain. Examples: 'penetration-testing', "
                    "'web-application-security', 'red-teaming', 'digital-forensics', "
                    "'cloud-security', 'api-security'. Use with or without `name`."
                ),
            },
        },
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        query = str(args.get("name", "")).strip()
        group_filter = str(args.get("group", "")).strip().lower()
        list_mode = not query or query.lower() in ("list", "")

        _, all_entries = _discover_skills(ctx.config, ctx.engagement)
        if not all_entries:
            return ToolResult("no skills found.")

        discovered = all_entries

        # ---- Filter ----
        if group_filter:
            discovered = [
                d for d in discovered
                if group_filter in d[4].lower() or group_filter in d[1].lower()
            ]
            if not discovered:
                return ToolResult(
                    f"no skills found under domain '{group_filter}'. "
                    f"Try calling without `group` to see available domains."
                )

        # ---- Search ----
        if not list_mode:
            ql = query.lower().replace(" ", "-").replace("_", "-")
            candidates = [d for d in discovered if ql in d[0].lower()]
            if not candidates:
                candidates = [
                    d for d in discovered
                    if any(ql in str(t).lower() for t in d[3] if t) or ql in d[2].lower()
                ]
            if len(candidates) == 1:
                return _load_skill_file(candidates[0])
            if candidates:
                return _format_skill_list(candidates, f"Matches for '{query}':", max_skills=20)
            return ToolResult(
                f"skill '{query}' not found among {len(discovered)} available skills."
            )

        return _format_skill_list(discovered, f"Available skills ({len(discovered)}):", max_skills=50)


def _read_frontmatter(path: Path) -> dict:
    """Extract YAML frontmatter from a markdown file. Returns {} on failure."""
    import yaml
    try:
        text = path.read_text()
        if not text.startswith("---"):
            return {}
        end = text.find("---", 3)
        if end == -1:
            return {}
        return yaml.safe_load(text[3:end]) or {}
    except Exception:
        return {}


def _load_skill_file(entry: tuple) -> ToolResult:
    """Load and return the full SKILL.md content."""
    kname, title, desc, tags, subd, path = entry
    read_path = path
    if read_path.is_dir():
        read_path = read_path / "SKILL.md"
    try:
        content = read_path.read_text()
        if read_path.name == "SKILL.md":
            # Full skill file from agentskills.io repo
            truncated = content[:24000]
            header = f"# SKILL: {title}\n(subdomain: {subd})\n"
            return ToolResult(header + truncated)
        else:
            # Flat .md file
            truncated = content[:24000]
            return ToolResult(f"# SKILL: {kname}\n\n{truncated}")
    except OSError as e:
        return ToolResult(f"error reading skill: {e}", is_error=True)


def _format_skill_list(entries: list, heading: str, max_skills: int = 50) -> ToolResult:
    """Format a list of skills grouped by subdomain."""
    grouped: dict[str, list] = {}
    for kname, title, desc, tags, subd, path in entries:
        bucket = subd or "(uncategorized)"
        grouped.setdefault(bucket, []).append((kname, title, desc, tags))

    lines = [heading, ""]
    shown = 0
    for bucket in sorted(grouped):
        skills_in_bucket = grouped[bucket]
        lines.append(f"## {bucket} ({len(skills_in_bucket)})")
        for kname, title, desc, tags in skills_in_bucket:
            if shown >= max_skills:
                break
            tag_str = ", ".join(str(t) for t in tags[:5] if t) if tags else ""
            d = desc[:120].replace("\n", " ") if desc else "(no description)"
            tag_suffix = f" [{tag_str}]" if tag_str else ""
            display = title if title != kname else kname
            lines.append(f"- **{display}** — {d}{tag_suffix}")
            shown += 1
        if shown >= max_skills:
            break
        lines.append("")

    if len(entries) > max_skills:
        lines.append(f"\n(Showing {max_skills} of {len(entries)} skills. "
                      f"Narrow your search with `name` or `group`.)")
    lines.append("\nTo load one, call `load_skill` with its exact `name`.")
    return ToolResult("\n".join(lines))


class WordlistTool(Tool):
    name = "wordlist"
    description = (
        "List or search local wordlists for fuzzing/brute-forcing (ffuf, gobuster, "
        "nuclei, hydra). Searches known SecLists/system locations and any configured "
        "dir, returning absolute paths to plug into a bash command. Call with no args "
        "to see the catalog grouped by category; pass `query` (e.g. 'directory', "
        "'subdomains', 'common', 'usernames') to find the best match. An empty result "
        "means none are installed — suggest the operator install SecLists."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Optional filter, e.g. 'directory', 'subdomains', 'common', "
                    "'usernames'. Omit to list the full catalog grouped by category."
                ),
            },
        },
    }

    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        from riftor.engagement.wordlists import (
            KNOWN_ROOTS,
            Wordlist,
            count_lines,
            discover,
            search,
        )

        extra = ctx.config.wordlists_dir if ctx.config is not None else None
        lists = discover(extra_dir=extra)
        if not lists:
            roots = list(KNOWN_ROOTS) + ([extra] if extra else [])
            shown = ", ".join(str(Path(r).expanduser()) for r in roots)
            return ToolResult(
                "no wordlists found. Searched: "
                + shown
                + ". Install SecLists or set `wordlists_dir` in config "
                + "(~/.config/riftor/config.toml)."
            )

        query = str(args.get("query") or "").strip()
        if query:
            matches = search(query, lists)
            if not matches:
                cats = sorted({w.category for w in lists})
                return ToolResult(
                    f"no wordlist matched '{query}'. Categories available: "
                    + ", ".join(cats)
                )
            lines = [f"# wordlists matching '{query}'"]
            for w in matches:
                n = count_lines(w.path)
                lines.append(f"- {w.path}  ({w.category}, {n if n is not None else '?'} lines)")
            return ToolResult("\n".join(lines)).truncated(ctx.max_result_chars)

        # No query: grouped catalog.
        by_cat: dict[str, list[Wordlist]] = {}
        for w in lists:
            by_cat.setdefault(w.category, []).append(w)
        out = [f"# {len(lists)} wordlists ({len(by_cat)} categories)"]
        for cat in sorted(by_cat):
            out.append(f"\n## {cat}")
            for w in sorted(by_cat[cat], key=lambda x: x.name):
                n = count_lines(w.path)
                out.append(f"- {w.name}  {w.path}  ({n if n is not None else '?'} lines)")
        if len(lists) >= 500:
            out.append("\n…list capped at 500; pass a `query` to narrow.")
        return ToolResult("\n".join(out)).truncated(ctx.max_result_chars)


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
