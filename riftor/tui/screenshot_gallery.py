"""Screenshot gallery modal — browse, view, and delete browser screenshots."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Label, ListItem, ListView, Static

from riftor.tui.theme import palette, css_variable_defaults

try:
    from textual_image.widget import Image as _InlineImage  # type: ignore
except Exception:  # noqa: BLE001
    _InlineImage = None


def _human_size(nbytes: int) -> str:
    if nbytes < 1024:
        return f"{nbytes} B"
    elif nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    else:
        return f"{nbytes / (1024 * 1024):.1f} MB"


def _safe_palette(widget) -> dict[str, str]:
    try:
        return palette(widget.app)
    except Exception:  # noqa: BLE001
        return css_variable_defaults()


class _ScreenshotItem(ListItem):
    """A single screenshot entry in the list view."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.screenshot_path = path
        stat = path.stat()
        self._label_text = self._format_label(path, stat.st_size, stat.st_mtime)

    @staticmethod
    def _format_label(path: Path, size: int, mtime: float) -> str:
        name = path.name
        hsize = _human_size(size)
        dt = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        return f"{name}    {hsize}    {dt}"

    def compose(self) -> ComposeResult:
        yield Label(self._label_text)


class _PreviewPane(Widget):
    """Right-side panel showing the selected screenshot."""

    def __init__(self, workdir: Path) -> None:
        super().__init__()
        self.workdir = workdir
        self._current: Path | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="gallery-preview-scroll"):
            yield Static("", id="gallery-preview-info")
            with Vertical(id="gallery-preview-image"):
                yield Static("", classes="note")

    def show(self, path: Path) -> None:
        self._current = path
        p = _safe_palette(self)
        info = self.query_one("#gallery-preview-info", Static)
        stat = path.stat()
        size = _human_size(stat.st_size)
        info.update(Text(f"{path.name}  ·  {size}", style=p["cyan"]))
        container = self.query_one("#gallery-preview-image", Vertical)
        container.remove_children()
        if _InlineImage is not None:
            try:
                container.mount(_InlineImage(str(path)))
            except Exception:  # noqa: BLE001 — terminal can't render; show link
                self._show_link(container, path, p)
        else:
            self._show_link(container, path, p)

    def clear(self) -> None:
        self._current = None
        self.query_one("#gallery-preview-info", Static).update("")
        container = self.query_one("#gallery-preview-image", Vertical)
        container.remove_children()
        container.mount(Static("select a screenshot to preview", classes="note"))

    @staticmethod
    def _show_link(container: Vertical, path: Path, p: dict) -> None:
        uri = path.resolve().as_uri()
        link = Text("open screenshot", style=f"underline {p['cyan']}")
        link.stylize(f"link {uri}")
        container.mount(Static(link, classes="note"))


class ScreenshotGalleryScreen(ModalScreen[None]):
    """Browse, preview, and delete screenshots from .riftor/screenshots/."""

    BINDINGS = [
        ("escape", "cancel", "Close"),
        ("d", "delete_current", "Delete"),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    def __init__(self, workdir: Path) -> None:
        super().__init__()
        self.workdir = workdir
        self._shots_dir = workdir / ".riftor" / "screenshots"
        self._deleting = False
        self._delete_target: Path | None = None

    def compose(self) -> ComposeResult:
        p = _safe_palette(self)
        with Vertical(id="gallery-box"):
            yield Static(Text("screenshots", style=f"bold {p['violet']}"), id="gallery-title")
            screenshots = sorted(
                self._shots_dir.glob("*.png"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            ) if self._shots_dir.exists() else []
            with Horizontal(id="gallery-main"):
                if screenshots:
                    yield ListView(
                        *(_ScreenshotItem(s) for s in screenshots),
                        id="gallery-list",
                    )
                    yield _PreviewPane(self.workdir)
                else:
                    yield Static(
                        "No screenshots yet.  Use browser_screenshot in a browser session.",
                        id="gallery-empty",
                        classes="note",
                    )
            with Horizontal(id="gallery-footer"):
                yield Label("↑↓ navigate · Enter preview · d delete · Esc close", classes="note")
                if screenshots:
                    yield Button("Delete", id="gallery-delete-btn", variant="error")
                    yield Button("Close", id="gallery-close-btn", variant="primary")

    def on_mount(self) -> None:
        if self._shots_dir.exists():
            items = self.query("#gallery-list > ListItem")
            if items:
                lv = self.query_one("#gallery-list", ListView)
                lv.focus()
                self._show_selected()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.item, _ScreenshotItem):
            self._show_item(event.item.screenshot_path)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, _ScreenshotItem):
            self._show_item(event.item.screenshot_path)

    def _current_item(self) -> _ScreenshotItem | None:
        try:
            lv = self.query_one("#gallery-list", ListView)
        except Exception:  # noqa: BLE001
            return None
        if lv.index is None or lv.index >= len(lv.children):
            return None
        child = list(lv.children)[lv.index]
        return child if isinstance(child, _ScreenshotItem) else None

    def _show_selected(self) -> None:
        item = self._current_item()
        if item is not None:
            self._show_item(item.screenshot_path)

    def _show_item(self, path: Path) -> None:
        try:
            preview = self.query_one(_PreviewPane)
        except Exception:  # noqa: BLE001
            return
        preview.show(path)

    def action_cancel(self) -> None:
        if self._deleting:
            self._deleting = False
            self._delete_target = None
            self._set_status("")
            return
        self.dismiss(None)

    def action_delete_current(self) -> None:
        item = self._current_item()
        if item is None:
            return
        path = item.screenshot_path
        if not self._deleting or self._delete_target != path:
            self._deleting = True
            self._delete_target = path
            p = _safe_palette(self)
            self._set_status(Text(f"press d again to delete {path.name}  ·  Esc to cancel", style=p["danger"]))
            return
        self._delete(path)

    def action_cursor_down(self) -> None:
        try:
            lv = self.query_one("#gallery-list", ListView)
            lv.action_cursor_down()
        except Exception:  # noqa: BLE001
            pass

    def action_cursor_up(self) -> None:
        try:
            lv = self.query_one("#gallery-list", ListView)
            lv.action_cursor_up()
        except Exception:  # noqa: BLE001
            pass

    @work(group="gallery")
    async def _delete(self, path: Path) -> None:
        p = _safe_palette(self)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            self._set_status(Text(f"error deleting {path.name}: {exc}", style=p["danger"]))
            self._deleting = False
            self._delete_target = None
            return
        self._deleting = False
        self._delete_target = None
        self._set_status(Text(f"deleted {path.name}", style=p["cyan"]))
        self._rebuild_list()
        if not self._shots_dir.exists() or not list(self._shots_dir.glob("*.png")):
            self.dismiss(None)

    def _rebuild_list(self) -> None:
        main = self.query_one("#gallery-main", Horizontal)
        try:
            lv = self.query_one("#gallery-list", ListView)
        except Exception:  # noqa: BLE001
            return
        screenshots = sorted(
            self._shots_dir.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ) if self._shots_dir.exists() else []
        lv.clear()
        if screenshots:
            for s in screenshots:
                lv.mount(_ScreenshotItem(s))
            if lv.index is not None and lv.index >= len(lv.children):
                lv.index = len(lv.children) - 1
            self._show_selected()
            try:
                self.query_one("#gallery-delete-btn", Button).disabled = False
            except Exception:  # noqa: BLE001
                pass
        else:
            main.remove_children()
            main.mount(Static(
                "No screenshots yet.  Use browser_screenshot in a browser session.",
                id="gallery-empty",
                classes="note",
            ))
            try:
                footer = self.query_one("#gallery-footer", Horizontal)
                footer.remove_children()
                footer.mount(Label("No screenshots — Esc to close", classes="note"))
            except Exception:  # noqa: BLE001
                pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "gallery-delete-btn":
            self.action_delete_current()
        elif event.button.id == "gallery-close-btn":
            self.dismiss(None)

    def _set_status(self, message: str | Text) -> None:
        try:
            title = self.query_one("#gallery-title", Static)
            if isinstance(message, str):
                p = _safe_palette(self)
                title.update(Text(message, style=p["muted"]))
            else:
                title.update(message)
        except Exception:  # noqa: BLE001
            pass
