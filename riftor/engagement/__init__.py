"""Engagement: scope + persistent state + methodology checklist."""

from __future__ import annotations

from pathlib import Path

from riftor.engagement.methodology import MethodologyItem, format_methodology_block
from riftor.engagement.scope import Scope, Target
from riftor.engagement.state import Store
from riftor.engagement.templates import ACTIVE_TEMPLATE_META_KEY


class Engagement:
    """The live engagement: scope guardrail, sqlite state, and methodology checklist."""

    def __init__(self, workdir: Path) -> None:
        self.dir = Path(workdir) / ".riftor"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.dir / "engagement.db")
        self.store.seed_methodology_if_empty()
        self.scope = Scope()
        self.enforce = self.store.get_meta("enforce", "1") == "1"
        self.dry_run = self.store.get_meta("scope_dry_run", "0") == "1"
        for target, mode in self.store.list_scope():
            self.scope.add(target, mode)

    # -- scope ------------------------------------------------------------------
    def add_scope(self, raw: str, mode: str = "in") -> Target:
        target = self.scope.add(raw, mode)
        self.store.add_scope(target.raw, mode)
        self.store.log_activity("scope_add", f"{mode}: {target.raw}")
        return target

    def remove_scope(self, raw: str) -> bool:
        ok = self.scope.remove(raw)
        self.store.remove_scope(Target.parse(raw).raw)
        self.store.log_activity("scope_remove", raw)
        return ok

    def clear_scope(self) -> None:
        self.scope.clear()
        self.store.clear_scope()
        self.store.log_activity("scope_clear", "")

    def set_enforce(self, on: bool) -> None:
        self.enforce = on
        self.store.set_meta("enforce", "1" if on else "0")
        self.store.log_activity("scope_enforce", "on" if on else "off")

    def set_dry_run(self, on: bool) -> None:
        self.dry_run = on
        self.store.set_meta("scope_dry_run", "1" if on else "0")
        self.store.log_activity("scope_dry_run", "on" if on else "off")

    def violations(self, text: str) -> list[str]:
        if not self.enforce:
            return []
        return self.scope.violations(text)

    def scope_count(self) -> int:
        return len(self.scope.in_scope)

    def export_scope(self) -> str:
        lines = [t.raw for t in self.scope.in_scope]
        lines += [f"out:{t.raw}" for t in self.scope.out_of_scope]
        return "\n".join(lines) + ("\n" if lines else "")

    def import_scope(self, text: str) -> tuple[int, int]:
        added_in = added_out = 0
        for raw in (text or "").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            mode = "in"
            if line.lower().startswith("out:"):
                mode, line = "out", line[4:].strip()
            elif line.lower().startswith("in:"):
                line = line[3:].strip()
            if not line:
                continue
            self.add_scope(line, mode)
            if mode == "in":
                added_in += 1
            else:
                added_out += 1
        return added_in, added_out

    # -- methodology ------------------------------------------------------------
    def list_methodology(self) -> list[MethodologyItem]:
        return [
            MethodologyItem(
                category=r["category"],
                name=r["name"],
                checked=bool(r["checked"]),
                notes=r.get("notes") or "",
            )
            for r in self.store.list_methodology()
        ]

    def methodology_progress(self) -> tuple[int, int]:
        return self.store.methodology_progress()

    def check_methodology(self, name: str, *, notes: str = "") -> bool:
        return self.store.check_methodology(name, notes=notes)

    def auto_tick_methodology(self, tool_name: str, preview: str = "") -> bool:
        from riftor.engagement.methodology import auto_tick_for_tool

        item = auto_tick_for_tool(tool_name, preview)
        if item:
            return self.store.auto_tick_methodology(item)
        return False

    def methodology_block(self) -> str:
        return format_methodology_block(self.list_methodology())

    # -- template ---------------------------------------------------------------
    def set_template(self, key: str) -> None:
        key = (key or "").strip()
        self.store.set_meta(ACTIVE_TEMPLATE_META_KEY, key)
        self.store.log_activity("template_apply", key or "(cleared)")

    def active_template(self) -> str | None:
        val = self.store.get_meta(ACTIVE_TEMPLATE_META_KEY, "")
        return val or None

    # -- findings / services ----------------------------------------------------
    def add_finding(self, **kwargs) -> int:
        fid = self.store.add_finding(**kwargs)
        self.store.log_activity(
            "finding_add", f"#{fid} [{kwargs.get('severity', '?')}] {kwargs.get('title', '')}"
        )
        return fid

    def add_finding_dedup(self, *, dedup: str = "skip", **kwargs) -> tuple[int, str]:
        if dedup == "allow-all":
            return self.add_finding(**kwargs), "added"
        existing = self.store.find_finding_id(
            kwargs.get("title", ""),
            kwargs.get("host", ""),
            kwargs.get("severity", ""),
            kwargs.get("evidence", ""),
        )
        if existing is None:
            return self.add_finding(**kwargs), "added"
        if dedup == "merge":
            merge_fields: dict = {}
            for fld in (
                "evidence", "recommendation", "cvss", "tags", "notes",
                "confidence", "verification_method",
            ):
                val = kwargs.get(fld)
                if val is not None and val != "":
                    merge_fields[fld] = val
            if merge_fields:
                self.store.update_finding(existing, **merge_fields)
            self.store.log_activity("finding_merge", f"#{existing}")
            return existing, "merged"
        return existing, "skipped"

    def add_service(self, **kwargs) -> int:
        return self.store.add_service(**kwargs)

    def add_service_dedup(self, *, dedup: str = "skip", **kwargs) -> tuple[int | None, str]:
        if dedup != "allow-all" and self.store.service_exists(
            kwargs.get("host", ""), kwargs.get("port"), kwargs.get("proto", "tcp")
        ):
            return None, "skipped"
        return self.store.add_service(**kwargs), "added"

    def findings_count(self) -> int:
        return self.store.count_findings()

    def close(self) -> None:
        self.store.close()


__all__ = ["Engagement", "Scope", "Target", "Store", "MethodologyItem"]
