"""Engagement: scope + persistent state + RIFT stage, tied together."""

from __future__ import annotations

from pathlib import Path

from riftor.engagement.scope import Scope, Target
from riftor.engagement.state import Store

VALID_STAGES = ("R", "I", "F", "T")


class Engagement:
    """The live engagement: scope guardrail, sqlite state, and current stage."""

    def __init__(self, workdir: Path) -> None:
        self.dir = Path(workdir) / ".riftor"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.dir / "engagement.db")
        self.scope = Scope()
        self.enforce = self.store.get_meta("enforce", "1") == "1"
        self.stage = self.store.get_meta("stage", "R") or "R"
        for target, mode in self.store.list_scope():
            self.scope.add(target, mode)

    # -- scope ------------------------------------------------------------------
    def add_scope(self, raw: str, mode: str = "in") -> Target:
        target = self.scope.add(raw, mode)
        self.store.add_scope(target.raw, mode)
        return target

    def remove_scope(self, raw: str) -> bool:
        ok = self.scope.remove(raw)
        self.store.remove_scope(Target.parse(raw).raw)
        return ok

    def clear_scope(self) -> None:
        self.scope.clear()
        self.store.clear_scope()

    def set_enforce(self, on: bool) -> None:
        self.enforce = on
        self.store.set_meta("enforce", "1" if on else "0")

    def violations(self, text: str) -> list[str]:
        if not self.enforce:
            return []
        return self.scope.violations(text)

    def scope_count(self) -> int:
        return len(self.scope.in_scope)

    # -- stage ------------------------------------------------------------------
    def set_stage(self, letter: str) -> bool:
        letter = (letter or "").strip().upper()[:1]
        if letter in VALID_STAGES:
            self.stage = letter
            self.store.set_meta("stage", letter)
            return True
        return False

    # -- findings / services ----------------------------------------------------
    def add_finding(self, **kwargs) -> int:
        kwargs.setdefault("stage", self.stage)
        return self.store.add_finding(**kwargs)

    def add_service(self, **kwargs) -> int:
        return self.store.add_service(**kwargs)

    def findings_count(self) -> int:
        return self.store.count_findings()


__all__ = ["Engagement", "Scope", "Target", "Store", "VALID_STAGES"]
