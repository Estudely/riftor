"""Custom widgets: banner, status bar, flock panel, and command dropdown."""

from __future__ import annotations

import difflib

from rich.text import Text
from textual.timer import Timer
from textual.widgets import DataTable, ListItem, ListView, Static

from riftor.tui.theme import palette

PULSE_FRAMES = ["●", "○", "◎", "◉", "◎", "○", "●"]


class Banner(Static):
    def render(self) -> Text:
        p = palette(self.app)
        t = Text()
        t.append("riftor", style=f"bold {p['violet']}")
        t.append("  ▍  ", style=p["cyan"])
        t.append("authorized security testing", style=f"dim {p['muted']}")
        return t


class PulseSpinner(Static):
    """Animated pulse spinner — ● → ○ → ◎ ◉."""

    FRAMES = PULSE_FRAMES

    def __init__(
        self,
        label: str = "",
        *,
        classes: str = "",
        id: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(classes=classes, id=id, **kwargs)
        self.label = label
        self._frame_idx = 0
        self._timer: Timer | None = None

    def start(self, label: str | None = None) -> None:
        if label is not None:
            self.label = label
        if self._timer is not None:
            return
        self._timer = self.set_interval(0.12, self._tick)

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def set_label(self, label: str) -> None:
        self.label = label
        self._render_frame()

    def _tick(self) -> None:
        self._frame_idx = (self._frame_idx + 1) % len(self.FRAMES)
        self._render_frame()

    def _render_frame(self) -> None:
        self.update(
            Text(
                f"{self.FRAMES[self._frame_idx]} {self.label}",
                style=self.rich_style if self.rich_style else "",
            )
        )


class StatusBar(Static):
    def __init__(self, model: str, yolo: bool = False) -> None:
        super().__init__()
        self.model = model
        self.yolo = yolo
        self.busy = False
        self.scope_count = 0
        self.enforce = True
        self.dry_run = False
        self.findings = 0
        self.methodology_done = 0
        self.methodology_total = 0
        self.tokens = 0
        self.cost = 0.0
        self.ctx_pct = 0
        self.worker_tokens = 0
        self.worker_cost = 0.0
        self._spinner_frame = 0
        self._spinner_timer: Timer | None = None

    def on_mount(self) -> None:
        self.refresh_bar()

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self._start_spinner()
        else:
            self._stop_spinner()
        self.refresh_bar()

    def _start_spinner(self) -> None:
        if self._spinner_timer is not None:
            return
        self._spinner_timer = self.set_interval(0.12, self._spin_tick)

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _spin_tick(self) -> None:
        self._spinner_frame = (self._spinner_frame + 1) % len(PULSE_FRAMES)
        self.refresh_bar()

    def set_model(self, model: str) -> None:
        self.model = model
        self.refresh_bar()

    def set_yolo(self, yolo: bool) -> None:
        self.yolo = yolo
        self.refresh_bar()

    def set_scope(self, count: int, enforce: bool, dry_run: bool = False) -> None:
        self.scope_count = count
        self.enforce = enforce
        self.dry_run = dry_run
        self.refresh_bar()

    def set_findings(self, count: int) -> None:
        self.findings = count
        self.refresh_bar()

    def set_methodology(self, done: int, total: int) -> None:
        self.methodology_done = done
        self.methodology_total = total
        self.refresh_bar()

    def set_usage(self, tokens: int, cost: float) -> None:
        self.tokens = tokens
        self.cost = cost
        self.refresh_bar()

    def set_context(self, pct: int) -> None:
        self.ctx_pct = pct
        self.refresh_bar()

    def set_worker_usage(self, tokens: int, cost: float) -> None:
        self.worker_tokens = tokens
        self.worker_cost = cost
        self.refresh_bar()

    def refresh_bar(self) -> None:
        p = palette(self.app)
        t = Text()
        t.append("scope:", style=p["dim"])
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
        if self.methodology_total:
            t.append("   checklist:", style=p["dim"])
            pct = int(self.methodology_done / self.methodology_total * 100) if self.methodology_total else 0
            t.append(f"{self.methodology_done}/{self.methodology_total}", style=p["cyan"])
            t.append(f" ({pct}%)", style=p["muted"])
        if self.tokens:
            t.append("   tok:", style=p["dim"])
            tok_label = f"{self.tokens / 1000:.1f}k" if self.tokens >= 1000 else str(self.tokens)
            t.append(tok_label, style=p["muted"])
            if self.cost:
                t.append(f" ${self.cost:.3f}", style=p["muted"])
        if self.worker_tokens:
            t.append("   ⚙", style=p["dim"])
            w_label = (
                f"{self.worker_tokens / 1000:.1f}k"
                if self.worker_tokens >= 1000 else str(self.worker_tokens)
            )
            t.append(w_label, style=p["muted"])
            if self.worker_cost:
                t.append(f" ${self.worker_cost:.3f}", style=p["muted"])
        if self.ctx_pct >= 60:
            t.append("   ctx:", style=p["dim"])
            t.append(f"{self.ctx_pct}%", style=p["danger"] if self.ctx_pct >= 80 else p["magenta"])
        t.append("   model:", style=p["dim"])
        t.append(self.model, style=p["muted"])
        if self.yolo:
            t.append("   ⚡ yolo", style=f"bold {p['danger']}")
        if self.busy:
            frame = PULSE_FRAMES[self._spinner_frame]
            t.append(f"   {frame} running…", style=p["cyan"])
        self.update(t)


_FLOCK_STATE = {
    "queued": ("⋯", "queued"),
    "running": ("⟳", "run"),
    "detail": ("⟳", "run"),
    "done": ("✓", "done"),
    "timeout": ("✗", "t/o"),
    "error": ("✗", "err"),
}


def _fmt_tok(usage) -> str:
    if usage is None:
        return "—"
    n = usage.total_tokens
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class FlockPane(DataTable):
    """Live per-worker status table for an in-flight dispatch."""

    def __init__(self) -> None:
        super().__init__(zebra_stripes=False, cursor_type="none")
        self._state: dict[int, str] = {}
        self._cols: list = []

    def _ensure_columns(self) -> None:
        if not self._cols:
            self._cols = list(self.add_columns("#", "state", "task", "detail", "tok"))

    @property
    def worker_indices(self) -> set[int]:
        return set(self._state)

    def worker_state(self, idx: int) -> str:
        return self._state.get(idx, "")

    def update_worker(self, event: dict) -> None:
        self._ensure_columns()
        idx = int(event["worker"])
        state = str(event.get("state", ""))
        glyph, word = _FLOCK_STATE.get(state, ("?", "?"))
        task = str(event.get("task", "")).replace("\n", " ").strip()[:48]
        detail = str(event.get("detail", "") or "")[:40] or "—"
        tok = _fmt_tok(event.get("usage"))
        state_cell = f"{glyph} {word}"
        key = str(idx)
        new_row = idx not in self._state
        self._state[idx] = state
        if new_row:
            self.add_row(str(idx + 1), state_cell, task, detail, tok, key=key)
            return
        self.update_cell(key, self._cols[1], state_cell)
        self.update_cell(key, self._cols[2], task)
        self.update_cell(key, self._cols[3], detail)
        self.update_cell(key, self._cols[4], tok)


class CommandDropdown(Static):
    """Dropdown list of matching slash commands."""

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
