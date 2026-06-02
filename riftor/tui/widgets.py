"""Custom widgets: the riftor banner and the [R·I·F·T] status bar."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from riftor.tui.theme import palette

RIFT_STAGES = ["R", "I", "F", "T"]
STAGE_NAMES = {"R": "Recon", "I": "Intrusion", "F": "Foothold", "T": "Takeover"}


class Banner(Static):
    def render(self) -> Text:
        p = palette(self.app)
        t = Text()
        t.append("riftor", style=f"bold {p['violet']}")
        t.append("  ▍  ", style=p["cyan"])
        t.append("find the rift · open it · cross through", style=f"dim {p['muted']}")
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
        p = palette(self.app)
        t = Text()
        t.append("[ ", style=p["faint"])
        for i, stage in enumerate(RIFT_STAGES):
            t.append(stage, style=f"bold {p['cyan']}" if stage == self.stage else p["dim"])
            if i < len(RIFT_STAGES) - 1:
                t.append("·", style=p["faint"])
        t.append(" ]  ", style=p["faint"])
        t.append(STAGE_NAMES[self.stage], style=p["violet"])
        t.append("   scope:", style=p["dim"])
        if self.scope_count:
            t.append(str(self.scope_count), style=p["muted"])
            t.append("" if self.enforce else " (off)", style=p["danger"])
        else:
            t.append("none", style=p["danger"] if self.enforce else p["dim"])
        t.append("   finds:", style=p["dim"])
        t.append(str(self.findings), style=p["magenta"] if self.findings else p["muted"])
        t.append("   model:", style=p["dim"])
        t.append(self.model, style=p["muted"])
        t.append("   lore:", style=p["dim"])
        t.append("on" if self.lore else "off", style=p["muted"])
        if self.busy:
            t.append("   ⟳ opening rift…", style=p["cyan"])
        self.update(t)
