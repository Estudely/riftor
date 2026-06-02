"""Custom widgets: the riftor banner and the [R·I·F·T] status bar."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

RIFT_STAGES = ["R", "I", "F", "T"]
STAGE_NAMES = {"R": "Recon", "I": "Intrusion", "F": "Foothold", "T": "Takeover"}


class Banner(Static):
    def render(self) -> Text:
        t = Text()
        t.append("riftor", style="bold #a855f7")
        t.append("  ▍  ", style="#22d3ee")
        t.append("find the rift · open it · cross through", style="dim #8b8ba7")
        return t


class StatusBar(Static):
    def __init__(self, model: str, stage: str = "R", lore: bool = True) -> None:
        super().__init__()
        self.model = model
        self.stage = stage
        self.lore = lore
        self.busy = False
        self.scope_count = 0
        self.enforce = True
        self.findings = 0

    def on_mount(self) -> None:
        self.refresh_bar()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.refresh_bar()

    def set_model(self, model: str) -> None:
        self.model = model
        self.refresh_bar()

    def set_lore(self, lore: bool) -> None:
        self.lore = lore
        self.refresh_bar()

    def set_stage(self, stage: str) -> None:
        if stage in STAGE_NAMES:
            self.stage = stage
            self.refresh_bar()

    def set_scope(self, count: int, enforce: bool) -> None:
        self.scope_count = count
        self.enforce = enforce
        self.refresh_bar()

    def set_findings(self, count: int) -> None:
        self.findings = count
        self.refresh_bar()

    def refresh_bar(self) -> None:
        t = Text()
        t.append("[ ", style="#3a3a4a")
        for i, stage in enumerate(RIFT_STAGES):
            t.append(stage, style="bold #22d3ee" if stage == self.stage else "#4a4a5a")
            if i < len(RIFT_STAGES) - 1:
                t.append("·", style="#3a3a4a")
        t.append(" ]  ", style="#3a3a4a")
        t.append(STAGE_NAMES[self.stage], style="#a855f7")
        t.append("   scope:", style="#5a5a6a")
        if self.scope_count:
            t.append(str(self.scope_count), style="#8b8ba7")
            t.append("" if self.enforce else " (off)", style="#fca5a5")
        else:
            t.append("none", style="#fca5a5" if self.enforce else "#5a5a6a")
        t.append("   finds:", style="#5a5a6a")
        t.append(str(self.findings), style="#f0abfc" if self.findings else "#8b8ba7")
        t.append("   model:", style="#5a5a6a")
        t.append(self.model, style="#8b8ba7")
        t.append("   lore:", style="#5a5a6a")
        t.append("on" if self.lore else "off", style="#8b8ba7")
        if self.busy:
            t.append("   ⟳ opening rift…", style="#22d3ee")
        self.update(t)
