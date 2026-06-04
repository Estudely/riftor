"""Custom widgets: the riftor banner, the [R·I·F·T] status bar, and command dropdown."""

from __future__ import annotations

import difflib

from rich.text import Text
from textual.widgets import ListItem, ListView, Static

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
    def __init__(self, model: str, stage: str = "R", lore: bool = True, yolo: bool = False) -> None:
        super().__init__()
        self.model = model
        self.stage = stage
        self.lore = lore
        self.yolo = yolo
        self.busy = False
        self.scope_count = 0
        self.enforce = True
        self.dry_run = False
        self.findings = 0
        self.tokens = 0
        self.cost = 0.0
        self.ctx_pct = 0
        self.chakla_tokens = 0
        self.chakla_cost = 0.0

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

    def set_yolo(self, yolo: bool) -> None:
        self.yolo = yolo
        self.refresh_bar()

    def set_stage(self, stage: str) -> None:
        if stage in STAGE_NAMES:
            self.stage = stage
            self.refresh_bar()

    def set_scope(self, count: int, enforce: bool, dry_run: bool = False) -> None:
        self.scope_count = count
        self.enforce = enforce
        self.dry_run = dry_run
        self.refresh_bar()

    def set_findings(self, count: int) -> None:
        self.findings = count
        self.refresh_bar()

    def set_usage(self, tokens: int, cost: float) -> None:
        self.tokens = tokens
        self.cost = cost
        self.refresh_bar()

    def set_context(self, pct: int) -> None:
        self.ctx_pct = pct
        self.refresh_bar()

    def set_chakla_usage(self, tokens: int, cost: float) -> None:
        self.chakla_tokens = tokens
        self.chakla_cost = cost
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
            if self.dry_run:
                t.append(" (dry)", style=p["magenta"])
            elif not self.enforce:
                t.append(" (off)", style=p["danger"])
        else:
            t.append("none", style=p["danger"] if self.enforce else p["dim"])
        t.append("   finds:", style=p["dim"])
        t.append(str(self.findings), style=p["magenta"] if self.findings else p["muted"])
        if self.tokens:
            t.append("   tok:", style=p["dim"])
            tok_label = f"{self.tokens / 1000:.1f}k" if self.tokens >= 1000 else str(self.tokens)
            t.append(tok_label, style=p["muted"])
            if self.cost:
                t.append(f" ${self.cost:.3f}", style=p["muted"])
        if self.chakla_tokens:
            t.append("   🐦", style=p["dim"])
            ch_label = (f"{self.chakla_tokens / 1000:.1f}k"
                        if self.chakla_tokens >= 1000 else str(self.chakla_tokens))
            t.append(ch_label, style=p["muted"])
            if self.chakla_cost:
                t.append(f" ${self.chakla_cost:.3f}", style=p["muted"])
        if self.ctx_pct >= 60:
            t.append("   ctx:", style=p["dim"])
            t.append(f"{self.ctx_pct}%", style=p["danger"] if self.ctx_pct >= 80 else p["magenta"])
        t.append("   model:", style=p["dim"])
        t.append(self.model, style=p["muted"])
        t.append("   lore:", style=p["dim"])
        t.append("on" if self.lore else "off", style=p["muted"])
        if self.yolo:
            t.append("   ⚡ yolo", style=f"bold {p['danger']}")
        if self.busy:
            t.append("   ⟳ opening rift…", style=p["cyan"])
        self.update(t)


class CommandDropdown(Static):
    """Dropdown list of matching slash commands, shown above the input.

    Hidden by default (``display: none`` in CSS). When the user types ``/`` in the
    prompt, the app calls :meth:`filter` to show matching commands and adds the
    ``visible`` CSS class. Tab / Enter fill the input with the highlighted command;
    Escape dismisses the dropdown.
    """

    def __init__(self, commands: list[str], id: str = "cmd-dropdown") -> None:
        super().__init__("")
        self.id = id
        self._all_commands = commands
        self._filtered: list[str] = []
        self._list_view: ListView | None = None

    def compose(self):
        self._list_view = ListView()
        yield self._list_view

    @property
    def list_view(self) -> ListView:
        assert self._list_view is not None, "ListView not mounted yet"
        return self._list_view

    @property
    def highlighted_command(self) -> str | None:
        if not self._filtered:
            return None
        idx = self.list_view.index
        if idx is None:
            return None
        if 0 <= idx < len(self._filtered):
            return self._filtered[idx]
        return None

    @property
    def visible(self) -> bool:
        return self.has_class("visible")

    def show(self) -> None:
        self.add_class("visible")

    def hide(self) -> None:
        self.remove_class("visible")

    def filter(self, value: str) -> None:
        """Filter commands by *value* (the raw input text, e.g. ``/fin``).

        Populates the inner ``ListView`` with matching items. If nothing matches
        by prefix, falls back to fuzzy matching via ``difflib``.
        """
        prefix = value.lstrip("/").casefold()
        if not prefix:
            matches = list(self._all_commands)
        else:
            matches = [c for c in self._all_commands if c.casefold().startswith("/" + prefix)]
            if not matches:
                fuzzy = difflib.get_close_matches(
                    value, self._all_commands, n=8, cutoff=0.4
                )
                matches = fuzzy
        self._filtered = matches
        lv = self.list_view
        lv.clear()
        if matches:
            for cmd in matches:
                lv.append(ListItem(Static(Text(cmd))))
            lv.index = 0
        if matches:
            self.show()
        else:
            self.hide()
