"""The riftor Textual app.

An offensive-security agent. The model calls tools (bash/read/write/edit/grep/
glob/webfetch + engagement tools); dangerous tools prompt for permission (with a
diff preview for write/edit); scope-sensitive tools are blocked against
out-of-scope targets (with an explicit per-call override); RIFT stage, scope and
findings live in the engagement state and show in the status bar.
"""

from __future__ import annotations

import difflib
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.highlighter import RegexHighlighter
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits
from textual.command import Provider as CommandProvider
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, Input, Markdown, RichLog, Static

from riftor import tools
from riftor.agent import antiloop
from riftor.agent import session as sessions
from riftor.agent.context import Context
from riftor.agent.provider import Provider, ProviderError, ToolCall, Turn, Usage
from riftor.config import PERMISSIONS_PATH
from riftor.engagement import Engagement
from riftor.engagement.report import write_reports
from riftor.safety.audit import AuditLog
from riftor.safety.permissions import ConfirmScreen, Permissions
from riftor.tools import ToolContext, ToolResult
from riftor.tui.config_screen import ConfigScreen
from riftor.tui.screenshot_gallery import ScreenshotGalleryScreen
from riftor.tui.theme import THEMES, css_variable_defaults, palette
from riftor.tui.widgets import STAGE_NAMES, GENZ_STAGE_NAMES, GENZ_STAGE_LETTERS, Banner, CommandDropdown, FlockPane, PulseSpinner, StatusBar
from riftor.mesh.sidebar import MeshSidebar

# Optional inline image rendering. textual-image needs Python >= 3.12 and a
# graphics-capable terminal (Kitty/Sixel); import at module top per its detection
# requirement. A failed import simply means we show the screenshot path instead.
try:
    from textual_image.widget import Image as _InlineImage  # type: ignore
except Exception:  # noqa: BLE001 — never let a missing optional dep break startup
    _InlineImage = None

if TYPE_CHECKING:
    from riftor.config import Config

# Model-window estimates (tokens) for the context gauge, by provider prefix.
_CONTEXT_WINDOWS = {
    "anthropic/": 200_000, "openai/": 128_000, "openrouter/": 128_000,
    "codex/": 128_000,
    "gemini/": 1_000_000, "groq/": 128_000, "ollama": 8_192,
}
_DEFAULT_WINDOW = 128_000


class _ChipHighlighter(RegexHighlighter):
    """Paints the ``[Pasted ~N lines]`` chip in the active riftor palette.

    The style is read from the live theme on each render (via ``palette``) so the
    chip tracks the rift glow and re-tints when the operator switches themes.
    """

    _CHIP_RE = re.compile(r"\[Pasted ~\d+ lines(?: \(\d+\))?\]")

    def __init__(self, widget) -> None:
        super().__init__()
        self._widget = widget

    def highlight(self, text: Text) -> None:  # type: ignore[override]
        p = palette(self._widget.app)
        # Violet-on-panel chip with a faint border tint — matches the user/banner
        # accent rather than a clashing flat orange.
        style = f"{p['violet']} on {p['user-bg']}"
        for match in self._CHIP_RE.finditer(text.plain):
            text.stylize(style, match.start(), match.end())


class PromptInput(Input):
    """Single-line prompt that collapses a multi-line paste into a chip.

    Textual's ``Input`` keeps only the first line of a pasted block. Instead we
    show a compact ``[Pasted ~N lines]`` placeholder in the field and stash the
    full text, expanding it back on submit. Single-line pastes are unchanged.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # placeholder chip text -> the full pasted/recalled text it stands for
        self._pastes: dict[str, str] = {}
        self.highlighter = _ChipHighlighter(self)

    def _chip_for(self, text: str) -> str:
        """Register ``text`` under a unique chip placeholder and return the chip."""
        n = len(text.splitlines())
        base = f"[Pasted ~{n} lines]"
        placeholder = base
        counter = 2
        # Reuse the chip if it already maps to this exact text; otherwise pick a
        # fresh, unique placeholder so distinct pastes never collide.
        while placeholder in self._pastes and self._pastes[placeholder] != text:
            placeholder = f"[Pasted ~{n} lines ({counter})]"
            counter += 1
        self._pastes[placeholder] = text
        return placeholder

    def _on_paste(self, event: events.Paste) -> None:
        text = event.text
        if text and len(text.splitlines()) > 1:
            placeholder = self._chip_for(text)
            selection = self.selection
            if selection.is_empty:
                self.insert_text_at_cursor(placeholder)
            else:
                self.replace(placeholder, *selection)
            # Stop both bubbling and the base Input._on_paste (which would
            # otherwise also run via the MRO and insert the first line).
            event.stop()
            event.prevent_default()
        # Single-line paste: do nothing here and let the base Input._on_paste
        # run via the normal MRO dispatch (native behavior, unchanged).

    def expand(self, value: str) -> str:
        """Replace any known chip placeholders in ``value`` with their full text."""
        for placeholder, full in self._pastes.items():
            if placeholder in value:
                value = value.replace(placeholder, full)
        return value

    def register_recall(self, value: str) -> str:
        """Return a single-line view of ``value`` for history recall.

        Multi-line history entries are shown as a chip so the field stays on one
        line; ``expand`` turns the chip back into the full text on submit.
        """
        if "\n" in value:
            return self._chip_for(value)
        return value

    def reset_pastes(self) -> None:
        self._pastes.clear()


# Commands offered for fuzzy "did you mean" suggestions.
_COMMANDS = [
    "/help", "/clear", "/model", "/stage", "/scope", "/findings", "/finding",
    "/edit-finding", "/delete-finding", "/hosts", "/services", "/report",
    "/sessions", "/resume", "/new", "/theme", "/config", "/tools", "/permissions",
    "/lore", "/genz", "/cost", "/retry", "/continue", "/compact", "/copy", "/show",
    "/timeline", "/audit", "/export", "/conversation", "/doctor", "/review", "/hypotheses", "/lesson", "/lessons", "/memory", "/template", "/browser", "/clearlog", "/screenshots",     "/mesh", "/mesh-create", "/mesh-join", "/mesh-leave", "/mesh-invite", "/mesh-refresh", "/mesh-queue", "/mesh-processor", "/mesh-review", "/mesh-approve", "/mesh-reject", "/exit",
]

HELP = """\
**riftor — commands**

_Conversation_
- `/help` — show this help · `/clear` — clear conversation (`Ctrl+L`)
- `/retry` — re-run the last turn · `/continue [N]` — extend the step budget
- `/compact` — shrink old tool output to free context
- `/copy` — copy the last agent/tool output · `/show <id>` — expand a tool result
- `/cost` — token + cost for this session
- `/conversation` — export the full conversation as markdown

_Engagement_
- `/scope` — show scope · `/scope add 10.0.0.0/24 example.com` · `/scope out <t>`
  · `/scope rm <t>` · `/scope clear` · `/scope on|off|dry` · `/scope import <file>` · `/scope export [file]`
- `/stage [R|I|F|T]` — show or set the RIFT stage (Recon/Intrusion/Foothold/Takeover)
- `/findings` — list findings (severity-sorted) · `/finding <id>` — show one
- `/edit-finding <id> sev=high tags=...` · `/delete-finding <id>`
- `/hosts` · `/services` — discovered infrastructure
- `/report [md|html|json|sarif|both|all]` — write a report to `.riftor/reports/`
- `/timeline` — engagement activity log · `/export` — archive the whole engagement
- `/memory` — durable notes for this engagement · `/memory add [tag] <text>` · `/memory rm <id>` · `/memory clear`
- `/template [webapp|api|network|ad]` — apply an engagement playbook (sets stage + guides the agent) · `/template off`

_Settings & sessions_
- `/model [name]` — show or switch the model · `/theme [name]` (dark: rift/dusk/void/fracture/singularity · light: dawn/paper)
- `/config` — settings panel · `/permissions` — review allow/deny rules
- `/lore` — toggle the rift persona · `/genz` — toggle Gen Z / Chakla Baaj mode 🦅
- `/audit` — recent tool-call audit log
- `/doctor` — check which external recon tools (nmap/httpx/…) are installed
- `/browser [headed|headless|close]` — browser status / mode / teardown
- `/screenshots` — browse, view, and delete browser screenshots
- `/review` — self-critique findings for false positives before reporting
- `/hypotheses` — list tracked hypotheses (open leads)
- `/lesson <text>` — teach a durable lesson (persists across sessions)
- `/lessons` — list all saved lessons
- `/sessions` · `/resume <id>` · `/new` — manage saved sessions
- `/tools` — list tools · `/exit` — quit (`Ctrl+C`)

Type anything else to task the agent. `↑/↓` recall input · `PgUp/PgDn` scroll ·
`Esc` cancels a running response. Drag to select text, `Ctrl+Y` copies it.
"""


# (command, display, help) for the Ctrl+P command palette.
_PALETTE_COMMANDS = [
    ("/help", "Help", "Show all commands"),
    ("/findings", "Findings", "List findings (severity-sorted)"),
    ("/hosts", "Hosts", "List discovered hosts"),
    ("/services", "Services", "List discovered services"),
    ("/report both", "Report", "Write a report (md + html)"),
    ("/report all", "Report (all formats)", "md + html + json + sarif"),
    ("/timeline", "Timeline", "Engagement activity log"),
    ("/export", "Export engagement", "Archive the whole engagement"),
    ("/conversation", "Export conversation", "Save conversation as markdown"),
    ("/permissions", "Permissions", "Review allow/deny rules"),
    ("/doctor", "Doctor", "Check installed recon tools"),
    ("/audit", "Audit log", "Recent tool-call audit entries"),
    ("/cost", "Cost", "Token + cost for this session"),
    ("/compact", "Compact context", "Shrink old tool output"),
    ("/retry", "Retry", "Re-run the last turn"),
    ("/config", "Config", "Open the settings panel"),
    ("/new", "New session", "Start a fresh conversation"),
    ("/clear", "Clear", "Clear the conversation"),
    ("/screenshots", "Screenshots", "Browse, view, and delete screenshots"),
    ("/memory", "Memory", "Durable notes for this engagement"),
    ("/template", "Template", "Apply an engagement playbook"),
]


class RiftorCommands(CommandProvider):
    """Surfaces riftor's slash commands in the Ctrl+P command palette."""

    async def search(self, query: str) -> Hits:  # type: ignore[override]
        matcher = self.matcher(query)
        app = self.app
        for command, display, help_text in _PALETTE_COMMANDS:
            score = matcher.match(display)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(display),
                    (lambda c=command: app._command(c)),  # type: ignore[attr-defined]
                    help=help_text,
                )


class _RecoveryModal(ModalScreen[bool]):
    """Offered when an incomplete (crashed) session is found on startup."""

    BINDINGS = [
        ("escape", "dismiss(False)", "Start fresh"),
        ("r", "dismiss(True)", "Resume"),
    ]

    def __init__(self, session_id: str, title: str) -> None:
        super().__init__()
        self._sid = session_id
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="recovery-box"):
            yield Static(
                Text("⚠  Crashed Session Detected", style="bold #fbbf24"),
                id="recovery-title",
            )
            yield Static(
                Text(
                    f"Session {self._sid} ended unexpectedly.\n"
                    f"{self._title or '(empty)'}"
                ),
                id="recovery-detail",
            )
            with Horizontal(id="recovery-buttons"):
                yield Button("Resume (r)", id="resume", variant="success")
                yield Button("Start fresh (esc)", id="fresh", variant="error")

    def on_mount(self) -> None:
        self.query_one("#resume", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "resume")


class RiftorApp(App):
    CSS_PATH = "themes/rift.tcss"
    TITLE = "riftor"
    COMMANDS = App.COMMANDS | {RiftorCommands}  # type: ignore[arg-type]

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
        ("escape", "cancel", "Cancel"),
        # Textual binds ctrl+c → copy-selection by default, but we use ctrl+c
        # for quit; expose the built-in copy action on ctrl+y instead so a
        # mouse drag-selection can be copied to the clipboard (via OSC-52).
        Binding("ctrl+y", "screen.copy_text", "Copy selection", show=False),
        Binding("pageup", "scroll_chat('pageup')", "Scroll up", show=False),
        Binding("pagedown", "scroll_chat('pagedown')", "Scroll down", show=False),
        Binding("ctrl+home", "scroll_chat('home')", "Top", show=False),
        Binding("ctrl+end", "scroll_chat('end')", "Bottom", show=False),
    ]

    def __init__(self, config: "Config", workdir: Path | None = None, yolo: bool = False) -> None:
        super().__init__()
        self.config = config
        self.workdir = workdir or Path.cwd()
        self.yolo = yolo
        self.context = Context(lore=config.lore, genz=config.genz, workdir=self.workdir)
        self.provider = Provider(config)
        self._plugin_errors = tools.register_plugins(config)
        self.tools = tools.all_tools()
        self.tool_schemas = tools.schemas()
        self.engagement = Engagement(self.workdir)
        self.permissions = Permissions.load(PERMISSIONS_PATH)
        self.audit = AuditLog()
        self.max_steps = config.max_steps
        self.session_id = sessions.new_id()
        # input history + last-output tracking + rate limiting
        self._history: list[str] = []
        self._history_idx: int | None = None
        self._shell_history: list[str] = []
        self._tool_results: dict[int, str] = {}
        self._last_output: str = ""
        self._last_user_text: str | None = None
        self.usage = Usage()
        self.chakla_usage = Usage()
        self._flock: tuple[Static, FlockPane] | None = None  # (header, table) while a dispatch is live
        self._spinner: PulseSpinner | None = None  # chat-area spinner while agent is running
        self.toolctx = ToolContext(
            workdir=self.workdir,
            engagement=self.engagement,
            max_result_chars=config.max_result_chars,
            config=self.config,
            permissions=self.permissions,
            audit=self.audit,
            yolo=self.yolo,
            progress=self._on_chakla_progress,
        )
        self._rate_times: list[float] = []
        self._autoscroll = True
        self._browser_hint_shown = False

    def compose(self) -> ComposeResult:
        yield Banner(genz=self.config.genz, id="banner")
        yield Static(id="cwd-header")
        with Horizontal(id="main-row"):
            yield VerticalScroll(id="chat")
            yield MeshSidebar(id="mesh-sidebar")
        yield Collapsible(
            RichLog(id="shell-log", highlight=True, markup=False),
            id="shell-pane",
            title="Shell output",
            collapsed=True,
        )
        yield StatusBar(self.config.model, lore=self.config.lore, yolo=self.yolo, genz=self.config.genz)
        yield CommandDropdown(_COMMANDS, id="cmd-dropdown")
        yield PromptInput(placeholder="task riftor — or /help", id="prompt")

    def get_css_variables(self) -> dict[str, str]:
        variables = dict(css_variable_defaults())
        variables.update(super().get_css_variables())
        return variables

    def _apply_keybindings(self) -> None:
        """Apply operator key overrides from keybindings.toml (action → key)."""
        from riftor.config import load_keybindings

        overrides = load_keybindings()
        for action, key in overrides.items():
            try:
                self.bind(key, action, description=action.replace("action_", ""))
            except Exception:  # noqa: BLE001 — a bad override must not crash startup
                continue

    def _apply_theme(self, name: str) -> None:
        if name not in THEMES:
            name = "rift"
        self.theme = name
        self.refresh_css(animate=False)
        try:
            self.query_one(Banner).refresh()
            self.status.refresh_bar()
            self._refresh_cwd_header()
        except Exception:  # noqa: BLE001 — widgets may not be mounted yet
            pass

    async def on_mount(self) -> None:
        for theme in THEMES.values():
            self.register_theme(theme)
        self._apply_keybindings()
        self._apply_theme(self.config.theme)
        self._refresh_cwd_header()
        self.query_one("#prompt", PromptInput).focus()
        self.status.set_stage(self.engagement.stage)
        self._refresh_status()
        warning = self.config.model_warning()
        if warning:
            self._note("⚠ " + warning)
        for err in getattr(self, "_plugin_errors", []):
            self._note(f"plugin '{err.module}' skipped: {err.error.splitlines()[-1]}")
        self._toolchain_heads_up()
        # Initialize mesh P2P collaboration
        from riftor.mesh import MeshManager
        from riftor.mesh.commands import register_mesh_commands

        try:
            self.mesh_manager = MeshManager()
            await self.mesh_manager.start()
            identity = await self.mesh_manager.create_identity()
            sidebar = self.query_one(MeshSidebar)
            sidebar.set_manager(self.mesh_manager)
            sidebar.update_connection_status(True)
            register_mesh_commands(self, self.mesh_manager)
            self._wire_mesh_events(self.mesh_manager, sidebar)
            self._note(f"Mesh ready: {identity.get('node_id', 'unknown')[:12]}...")
        except RuntimeError as e:
            self._note(f"Mesh unavailable (daemon not found): {e}")
        except Exception as e:
            self._note(f"Mesh unavailable: {e}")
        # If the previous run crashed (incomplete session), prompt for recovery
        # after mount; otherwise resume the latest complete session as normal.
        if sessions.find_incomplete(self.workdir):
            self.set_timer(0.1, self._offer_recovery)
        elif not self._resume_latest():
            self._note(
                "rift online · set scope with /scope add <target> before tasking the agent"
            )

    async def on_unmount(self) -> None:
        mgr = self.toolctx.browser
        if mgr is not None and mgr.launched:
            try:
                await mgr.close()
            except Exception:  # noqa: BLE001
                pass

    def _toolchain_heads_up(self) -> None:
        """One-line note if recon tools are missing — surfaced up front, not mid-task."""
        from riftor.engagement.doctor import check_toolchain, summarize

        s = summarize(check_toolchain())
        if s["missing"]:
            self._note(
                f"⚠ {s['missing']}/{s['total']} recon tools not on PATH "
                f"({', '.join(s['missing_names'][:4])}{'…' if s['missing'] > 4 else ''}) "
                "· /doctor for details"
            )

    def _resume_latest(self) -> bool:
        data = sessions.latest(self.workdir)
        if not data or not data.get("messages"):
            return False
        self.session_id = data["id"]
        self.context.load(data["messages"])
        self.context.repair()
        self._replay_transcript(self.context.messages)
        note = f"resumed session {data['id']} ({len(data['messages'])} messages) · /new for fresh"
        if not data.get("complete", True):
            note += "  ⚠ previous run ended mid-task — /retry to resume or /continue"
        self._note(note)
        return True

    def _refresh_cwd_header(self) -> None:
        try:
            cwd_header = self.query_one("#cwd-header", Static)
            p = self._pal()
            cwd_header.update(Text.assemble(
                ("cwd: ", f"bold {p['violet']}"),
                (str(self.workdir), f"{p['muted']}"),
            ))
        except Exception:  # noqa: BLE001 — widget may not be mounted yet
            pass

    def _refresh_status(self) -> None:
        self.status.set_stage(self.engagement.stage)
        self.status.set_scope(
            self.engagement.scope_count(), self.engagement.enforce, self.engagement.dry_run
        )
        self.status.set_findings(self.engagement.findings_count())
        self.status.set_yolo(self.yolo)

    def _context_window(self) -> int:
        for prefix, window in _CONTEXT_WINDOWS.items():
            if self.config.model.startswith(prefix):
                return window
        return _DEFAULT_WINDOW

    def _refresh_usage(self) -> None:
        self.status.set_usage(self.usage.total_tokens, self.usage.cost)
        self.status.set_chakla_usage(self.chakla_usage.total_tokens, self.chakla_usage.cost)
        pct = int(self.context.estimated_tokens() / self._context_window() * 100)
        self.status.set_context(min(pct, 999))

    def _on_chakla_progress(self, event: dict) -> None:
        """Render a live worker progress event. Runs on the UI task (the agent
        loop is @work(exclusive=True), async) so widget mutation is direct."""
        if self._flock is None:
            header = Static(classes="flock-header")
            table = FlockPane()
            self._flock = (header, table)
            self.chat.mount(header)
            self.chat.mount(table)
            self._scroll_if_following()
        header, table = self._flock
        table.update_worker(event)
        if event.get("state") in ("done", "timeout", "error"):
            usage = event.get("usage")
            if usage is not None:
                self.chakla_usage.add(usage)
                self._refresh_usage()
        header.update(Text(self._flock_header_text(table), style=self._pal()["violet"]))

    def _flock_header_text(self, table: FlockPane) -> str:
        # Count from the widget's tracked raw state (public accessors), NOT by
        # re-parsing rendered cells — so a glyph/label change can't break counts.
        indices = table.worker_indices
        done = sum(1 for i in indices if table.worker_state(i) in ("done", "timeout", "error"))
        run = sum(1 for i in indices if table.worker_state(i) in ("running", "detail"))
        return f"🦅 dispatch · {len(indices)} 🐦 · {done} done · {run} running"

    def _clear_flock(self) -> None:
        if self._flock is None:
            return
        header, table = self._flock
        self._flock = None
        try:
            header.remove()
            table.remove()
        except Exception:  # noqa: BLE001 — teardown must never crash the loop
            pass

    def _clear_spinner(self) -> None:
        if self._spinner is None:
            return
        spinner = self._spinner
        self._spinner = None
        try:
            spinner.stop()
            spinner.remove()
        except Exception:  # noqa: BLE001 — teardown must never crash the loop
            pass

    # ---- accessors -------------------------------------------------------------
    @property
    def chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    @property
    def status(self) -> StatusBar:
        return self.query_one(StatusBar)

    @property
    def cmd_dropdown(self) -> CommandDropdown:
        return self.query_one("#cmd-dropdown", CommandDropdown)

    # ---- mount helpers ---------------------------------------------------------
    def _pal(self) -> dict[str, str]:
        return palette(self)

    def _scroll_if_following(self) -> None:
        if self._autoscroll:
            self.chat.scroll_end(animate=False)

    def _note(self, text: str) -> None:
        self.chat.mount(Static(Text(text, style=f"italic {self._pal()['dim']}"), classes="note"))
        self._scroll_if_following()

    def _error(self, text: str) -> None:
        self.chat.mount(Static(Text(text, style=f"bold {self._pal()['danger']}"), classes="note"))
        self._scroll_if_following()

    def _markdown(self, text: str) -> None:
        self.chat.mount(Markdown(text, classes="assistant"))
        self._scroll_if_following()

    def _add_user(self, text: str) -> None:
        self.chat.mount(Static(Text(text), classes="user"))
        self._scroll_if_following()

    @work(exclusive=True)
    async def _shell_cmd(self, text: str) -> None:
        from riftor.tools.core import run_shell

        command = text[1:].strip()
        if not command:
            return

        p = self._pal()
        shell_log = self.query_one("#shell-log", RichLog)
        shell_pane = self.query_one("#shell-pane", Collapsible)

        shell_log.write(Text(f"$ {command}", style=f"bold {p['violet']}"))

        try:
            result = await run_shell(command, str(self.workdir), timeout=120)
        except Exception as exc:
            shell_log.write(Text(f"[error: {exc}]", style=f"bold {p['danger']}"))
            self.audit.record("shell_error", command, allowed=False, is_error=True)
        else:
            self._shell_history.append(command)
            self.audit.record("shell_cmd", command, allowed=True)
            if result.stderr:
                shell_log.write(Text(result.stderr, style=p['danger']))
            if result.stdout:
                shell_log.write(Text(result.stdout))
            if result.exit_code != 0:
                shell_log.write(
                    Text(f"[exit {result.exit_code}]", style=f"bold {p['magenta']}")
                )

        shell_log.write("")

        shell_pane.title = f"Shell output — {len(self._shell_history)} commands"
        shell_pane.collapsed = False

    def _clearlog_cmd(self) -> None:
        """Clear the shell output log and collapse the pane."""
        shell_log = self.query_one("#shell-log", RichLog)
        shell_pane = self.query_one("#shell-pane", Collapsible)
        shell_log.clear()
        shell_pane.title = "Shell output"
        shell_pane.collapsed = True
        self._shell_history.clear()

    # ---- mesh commands ----------------------------------------------------------
    async def _mesh_cmd(self, arg: str = "") -> None:
        sub = arg.split()[0].lower() if arg else ""
        if sub == "mode":
            await self._mesh_mode_cmd(arg[len(sub):].strip())
            return
        if sub == "queue":
            await self._mesh_queue_cmd()
            return
        if sub == "processor":
            await self._mesh_processor_cmd()
            return
        if sub == "review":
            await self._mesh_review_cmd()
            return
        if sub == "approve":
            await self._mesh_approve_cmd(arg[len(sub):].strip())
            return
        if sub == "reject":
            await self._mesh_reject_cmd(arg[len(sub):].strip())
            return
        if sub == "test":
            await self._mesh_test_cmd()
            return
        if sub == "findings":
            await self._mesh_findings_cmd()
            return

        mgr = getattr(self, "mesh_manager", None)
        if mgr is None:
            self._note("Mesh not available — daemon binary not found")
            return
        if not mgr.running:
            self._note("Mesh not started")
            return
        state = mgr.current_state
        if state is None:
            lines = [
                "**Mesh** — connected, no active engagement",
                "",
                "/mesh-create <name> — start a collaborative engagement",
                "/mesh-join <invite> — join an existing engagement",
            ]
        else:
            lines = [
                f"**Mesh Engagement: {state.meta.name}**",
                f"Engagement ID: `{state.meta.id}`",
                f"Members: {len(state.members)} · Findings: {len(state.findings)}",
                f"Hosts: {len(state.hosts)} · Services: {len(state.services)}",
                "",
                "/mesh-invite — generate invite for peers",
                "/mesh-test — show P2P test command (cross-machine)",
                "/mesh-leave — leave this engagement",
                "/mesh-refresh — refresh state",
                "/mesh mode <autonomous|review|critical>",
                "/mesh queue — processor queue stats",
                "/mesh processor — processor status",
            ]
        self._markdown("\n".join(lines))

    async def _mesh_create_cmd(self, arg: str) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        name = arg.strip()
        if not name:
            self._note("usage: /mesh-create <name>")
            return
        try:
            meta = await mgr.create_engagement(name)
            sidebar = self.query_one(MeshSidebar)
            sidebar.update_connection_status(True, meta.name)
            self._note(f"Mesh engagement created: {meta.name} ({meta.id})")
        except Exception as e:
            self._error(f"Mesh create failed: {e}")

    async def _mesh_join_cmd(self, arg: str) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        invite = arg.strip()
        if not invite:
            self._note("usage: /mesh-join <invite>")
            return
        try:
            meta = await mgr.join_engagement(invite)
            sidebar = self.query_one(MeshSidebar)
            sidebar.update_connection_status(True, meta.name)
            await mgr.refresh_state()
            self._update_mesh_sidebar()
            self._note(f"Joined mesh engagement: {meta.name} ({meta.id})")
        except Exception as e:
            self._error(f"Mesh join failed: {e}")

    async def _mesh_leave_cmd(self) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        try:
            await mgr.leave_engagement()
            sidebar = self.query_one(MeshSidebar)
            sidebar.update_connection_status(True)
            sidebar.update_members([])
            self._note("Left mesh engagement")
        except Exception as e:
            self._error(f"Mesh leave failed: {e}")

    async def _mesh_invite_cmd(self) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        state = mgr.current_state
        if state is None:
            self._note("No active engagement — /mesh-create first")
            return
        try:
            invite = await mgr.generate_invite(state.meta.id)
            self._note(f"Invite code: {invite}")
            self._last_output = invite
        except Exception as e:
            self._error(f"Mesh invite failed: {e}")

    async def _mesh_refresh_cmd(self) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        try:
            state = await mgr.refresh_state()
            self._update_mesh_sidebar()
            self._note(
                f"Mesh refreshed: {len(state.members)} members, "
                f"{len(state.findings)} findings"
            )
        except Exception as e:
            self._error(f"Mesh refresh failed: {e}")

    async def _mesh_mode_cmd(self, arg: str) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        mode = arg.strip()
        if mode not in ("autonomous", "review", "critical"):
            self._note("Usage: /mesh mode autonomous|review|critical")
            return
        try:
            new_mode = await mgr.set_processor_mode(mode)
            self._note(f"Processor mode: {new_mode}")
        except Exception as e:
            self._error(f"Failed to set mode: {e}")

    async def _mesh_queue_cmd(self) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        try:
            stats = await mgr.get_queue_stats()
            lines = [
                f"Pending: {stats.get('pending', 0)}",
                f"Processing: {stats.get('processing', 0)}",
                f"Completed: {stats.get('completed', 0)}",
                f"Failed: {stats.get('failed', 0)}",
            ]
            self._note("\n".join(lines))
        except Exception as e:
            self._error(f"Failed: {e}")

    async def _mesh_processor_cmd(self) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        try:
            stats = await mgr.get_queue_stats()
            mode = stats.get("mode", "unknown")
            workers = stats.get("worker_count", "?")
            cb = "OPEN" if stats.get("circuit_open") else "closed"
            self._note(f"Processor: {mode} | Workers: {workers} | Circuit: {cb}")
        except Exception as e:
            self._error(f"Failed: {e}")

    async def _mesh_review_cmd(self) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        try:
            decisions = await mgr.get_review_queue()
            if not decisions:
                self._note("No pending decisions")
                return
            lines = [f"{len(decisions)} pending decisions:"]
            for i, d in enumerate(decisions[:5]):
                title = d.get("finding", {}).get("title", "N/A")
                decision = d.get("decision", "N/A")
                sev = d.get("severity", "N/A")
                lines.append(f"  #{i+1}: {title} [{sev}] ({decision})")
            self._note("\n".join(lines))
        except Exception as e:
            self._error(f"Failed: {e}")

    async def _mesh_approve_cmd(self, arg: str) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        if not arg.strip():
            self._note("Usage: /mesh approve <submission_id>")
            return
        try:
            result = await mgr.approve_review(arg.strip())
            self._note(f"Approved: {result.get('status', 'ok')}")
        except Exception as e:
            self._error(f"Failed: {e}")

    async def _mesh_reject_cmd(self, arg: str) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        parts = arg.strip().split(" ", 1)
        if len(parts) < 2:
            self._note("Usage: /mesh reject <submission_id> <reason>")
            return
        try:
            result = await mgr.reject_review(parts[0], parts[1])
            self._note(f"Rejected: {result.get('status', 'ok')}")
        except Exception as e:
            self._error(f"Failed: {e}")

    async def _mesh_findings_cmd(self) -> None:
        mgr = getattr(self, "mesh_manager", None)
        state = mgr.current_state if mgr else None
        if not state or not state.findings:
            self._note("No findings yet")
            return
        lines = [f"**{len(state.findings)} findings:**", ""]
        for f in state.findings:
            if isinstance(f, dict):
                title = f.get('title','?')
                sev = f.get('severity','?')
            else:
                title = f.title
                sev = f.severity.value if hasattr(f.severity, 'value') else str(f.severity)
            lines.append(f"- **{title}** [{sev}]")
        self._markdown("\n".join(lines))

    async def _mesh_test_cmd(self) -> None:
        """Print the P2P addresses and a test command for cross-machine submission."""
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None or not mgr.running:
            self._note("Mesh not available")
            return
        state = mgr.current_state
        if state is None:
            self._note("No active engagement. Use /mesh-create first.")
            return
        try:
            addr = await mgr.get_p2p_addr()
            nid = addr.get("node_id", "?")
            addrs = addr.get("direct_addresses", [])
            eng_id = state.meta.id
            import json
            addrs_json = json.dumps(addrs)
            cmd = f'./riftor-meshd <<< \'{{"id":1,"method":"p2p_submit_remote","params":{{"node_id":"{nid}","addresses":{addrs_json},"engagement_id":"{eng_id}","submission":{{"type":"finding","data":{{"title":"Test","severity":"medium","target":"10.0.0.5","vuln_class":"test"}}}}}}}}\''
            lines = [
                f"NodeId: {nid}",
                f"Addresses: {addrs}",
                f"Engagement: {eng_id}",
                "",
                "Run this on another machine:",
                cmd,
            ]
            self._note("\n".join(lines))
        except Exception as e:
            self._error(f"Failed to get P2P info: {e}")

    def _update_mesh_sidebar(self) -> None:
        mgr = getattr(self, "mesh_manager", None)
        if mgr is None:
            return
        state = mgr.current_state
        if state is None:
            return
        sidebar = self.query_one(MeshSidebar)
        sidebar.update_members(state.members)

    def _wire_mesh_events(self, manager, sidebar: MeshSidebar) -> None:
        """Register live handlers for gossip-derived MeshEvent subtopics."""

        async def on_processed(event: str, data: dict) -> None:
            try:
                await manager.refresh_state()
                self._update_mesh_sidebar()
            except Exception:  # noqa: BLE001
                pass
            payload = data.get("payload") or {}
            key = payload.get("key") or payload.get("event") or "finding"
            sidebar.add_activity(f"\u2713 finding: {key}")

        async def on_activity(event: str, data: dict) -> None:
            payload = data.get("payload") or {}
            text = payload.get("message") or payload.get("event") or str(payload)
            sidebar.add_activity(text)

        async def on_presence(event: str, data: dict) -> None:
            payload = data.get("payload") or {}
            node_id = payload.get("node_id")
            ts = payload.get("ts", "")
            if node_id:
                manager.update_member_presence(node_id, ts)
            sidebar.update_members(manager.members)

        manager.events.on("processed", on_processed)
        manager.events.on("activity", on_activity)
        manager.events.on("presence", on_presence)

    async def _mount(self, widget) -> None:
        await self.chat.mount(widget)
        self._scroll_if_following()

    # ---- events ----------------------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Dropdown selection: if the dropdown is visible and the user hasn't
        # typed an exact command match, fill with the highlighted suggestion.
        # Exact matches pass through to normal command dispatch.
        if self.cmd_dropdown.visible:
            text = event.value.strip()
            if text not in _COMMANDS:
                cmd = self.cmd_dropdown.highlighted_command
                if cmd:
                    inp = self.query_one("#prompt", PromptInput)
                    inp.value = cmd + " "
                    inp.cursor_position = len(inp.value)
                    self.cmd_dropdown.hide()
                return
            self.cmd_dropdown.hide()

        inp = self.query_one("#prompt", PromptInput)
        # Expand any [Pasted ~N lines] chip back to its full text before use.
        text = inp.expand(event.value).strip()
        inp.clear()
        inp.reset_pastes()
        self._history_idx = None
        if not text:
            return
        if text.startswith("!"):
            self._shell_cmd(text)
            return
        if not text.startswith("/"):
            self._history.append(text)
        if text.startswith("/"):
            self._command(text)
            return
        self._add_user(text)
        self._agent(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Show the command dropdown when the user starts typing a slash command."""
        value = event.value
        if value.startswith("/") and " " not in value:
            self.cmd_dropdown.filter(value)
        else:
            self.cmd_dropdown.hide()

    def on_key(self, event) -> None:
        inp = self.query_one("#prompt", PromptInput)

        # Dropdown navigation — takes priority over history recall.
        if self.cmd_dropdown.visible and inp.has_focus:
            if event.key == "tab":
                cmd = self.cmd_dropdown.highlighted_command
                if cmd:
                    inp.value = cmd + " "
                    inp.cursor_position = len(inp.value)
                    self.cmd_dropdown.hide()
                event.prevent_default()
                return
            if event.key in ("up", "down"):
                lv = self.cmd_dropdown.list_view
                if event.key == "up":
                    lv.action_cursor_up()
                else:
                    lv.action_cursor_down()
                event.prevent_default()
                return
            if event.key == "escape":
                self.cmd_dropdown.hide()
                event.prevent_default()
                return

        # ↑/↓ recall previous prompts when the input is focused.
        if not inp.has_focus or event.key not in ("up", "down"):
            return
        if not self._history:
            return
        if event.key == "up":
            self._history_idx = (
                len(self._history) - 1 if self._history_idx is None
                else max(0, self._history_idx - 1)
            )
        else:  # down
            if self._history_idx is None:
                return
            self._history_idx += 1
            if self._history_idx >= len(self._history):
                self._history_idx = None
                inp.value = ""
                event.prevent_default()
                return
        # Multi-line history entries recall as a chip so the field stays one line.
        inp.value = inp.register_recall(self._history[self._history_idx])
        inp.cursor_position = len(inp.value)
        event.prevent_default()

    def action_scroll_chat(self, where: str) -> None:
        chat = self.chat
        if where == "pageup":
            chat.scroll_page_up()
            self._autoscroll = False
        elif where == "pagedown":
            chat.scroll_page_down()
            self._autoscroll = chat.scroll_offset.y >= chat.max_scroll_y - 2
        elif where == "home":
            chat.scroll_home()
            self._autoscroll = False
        elif where == "end":
            chat.scroll_end()
            self._autoscroll = True

    def _command(self, text: str) -> None:
        import asyncio
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "/help": lambda: self._markdown(HELP),
            "/tools": self._tools_cmd,
            "/clear": self.action_clear,
            "/lore": self._lore_cmd,
            "/genz": self._genz_cmd,
            "/model": lambda: self._model_cmd(arg),
            "/stage": lambda: self._set_stage(arg),
            "/scope": lambda: self._scope_cmd(arg),
            "/findings": self._show_findings,
            "/finding": lambda: self._show_finding(arg),
            "/edit-finding": lambda: self._edit_finding_cmd(arg),
            "/delete-finding": lambda: self._delete_finding_cmd(arg),
            "/hosts": self._hosts_cmd,
            "/services": self._services_cmd,
            "/report": lambda: self._report_cmd(arg),
            "/sessions": self._sessions_cmd,
            "/resume": lambda: self._resume_cmd(arg),
            "/new": self._new_session,
            "/theme": lambda: self._theme_cmd(arg),
            "/config": self._open_config,
            "/permissions": lambda: self._permissions_cmd(arg),
            "/cost": self._cost_cmd,
            "/retry": self._retry_cmd,
            "/continue": lambda: self._continue_cmd(arg),
            "/compact": self._compact_cmd,
            "/copy": self._copy_cmd,
            "/show": lambda: self._show_result(arg),
            "/timeline": self._timeline_cmd,
            "/audit": self._audit_cmd,
            "/export": self._export_cmd,
            "/conversation": self._conversation_cmd,
            "/doctor": self._doctor_cmd,
            "/browser": lambda: self._browser_cmd(arg),
            "/screenshots": self._screenshots_cmd,
            "/review": self._review_cmd,
            "/hypotheses": self._hypotheses_cmd,
            "/lesson": lambda: self._lesson_cmd(arg),
            "/lessons": self._lessons_cmd,
            "/memory": lambda: self._memory_cmd(arg),
            "/template": lambda: self._template_cmd(arg),
            "/clearlog": self._clearlog_cmd,
            "/mesh": lambda: asyncio.create_task(self._mesh_cmd(arg)),
            "/mesh-create": lambda: asyncio.create_task(self._mesh_create_cmd(arg)),
            "/mesh-join": lambda: asyncio.create_task(self._mesh_join_cmd(arg)),
            "/mesh-leave": lambda: asyncio.create_task(self._mesh_leave_cmd()),
            "/mesh-invite": lambda: asyncio.create_task(self._mesh_invite_cmd()),
            "/mesh-refresh": lambda: asyncio.create_task(self._mesh_refresh_cmd()),
            "/mesh-queue": lambda: asyncio.create_task(self._mesh_queue_cmd()),
            "/mesh-processor": lambda: asyncio.create_task(self._mesh_processor_cmd()),
            "/mesh-review": lambda: asyncio.create_task(self._mesh_review_cmd()),
            "/mesh-approve": lambda: asyncio.create_task(self._mesh_approve_cmd(arg)),
            "/mesh-reject": lambda: asyncio.create_task(self._mesh_reject_cmd(arg)),
        }
        if cmd in ("/exit", "/quit"):
            self._save_session()
            self.exit()
            return
        handler = handlers.get(cmd)
        if handler is not None:
            handler()
            return
        # fuzzy "did you mean"
        close = difflib.get_close_matches(cmd, _COMMANDS, n=3, cutoff=0.5)
        if close:
            self._note(f"unknown command: {cmd} — did you mean {', '.join(close)}?")
        else:
            self._note(f"unknown command: {cmd} — try /help")

    def _tools_cmd(self) -> None:
        listing = "\n".join(
            f"- `{t.name}`{'  ⚠ needs approval' if t.requires_permission else ''} — {t.description}"
            for t in self.tools
        )
        self._markdown(f"**tools**\n\n{listing}")

    def _lore_cmd(self) -> None:
        self.config.lore = not self.config.lore
        self.context.lore = self.config.lore
        self.status.set_lore(self.config.lore)
        if self.config.genz:
            self._note(f"lore {'engaged no cap' if self.config.lore else 'disengaged, say less'}")
        else:
            self._note(f"lore {'engaged' if self.config.lore else 'disengaged'}")

    def _genz_cmd(self) -> None:
        self.config.genz = not self.config.genz
        self.context.genz = self.config.genz
        self.status.set_genz(self.config.genz)
        try:
            self.query_one(Banner).set_genz(self.config.genz)
        except Exception:
            pass
        if self.config.genz:
            self._note("genz engaged fr 🦅")
        else:
            self._note("genz disengaged, back to normie mode")

    def _model_cmd(self, arg: str) -> None:
        if arg:
            self.config.model = arg
            self.provider = Provider(self.config)
            self.status.set_model(arg)
            self._note(f"model → {arg}")
            warning = self.config.model_warning()
            if warning:
                self._note("⚠ " + warning)
        else:
            self._note(f"model: {self.config.model}")

    def _theme_cmd(self, arg: str) -> None:
        name = arg.strip().lower()
        if not name:
            self._note(f"theme: {self.config.theme} · available: {', '.join(THEMES)}")
            return
        if name not in THEMES:
            self._note(f"unknown theme: {name} · available: {', '.join(THEMES)}")
            return
        self.config.theme = name
        self.config.save()
        self._apply_theme(name)
        self._note(f"theme → {name}")

    @work(group="config")
    async def _open_config(self) -> None:
        from riftor.config import ProviderCreds  # local import: keep app import-time light

        result = await self.push_screen_wait(ConfigScreen(self.config))
        if not isinstance(result, dict):
            self._note("config unchanged")
            return
        self.config.model = result["model"]
        self.config.temperature = result["temperature"]
        self.config.max_tokens = result["max_tokens"]
        self.config.max_steps = result.get("max_steps", self.config.max_steps)
        self.max_steps = self.config.max_steps
        self.config.lore = result["lore"]
        self.config.genz = result.get("genz", self.config.genz)
        self.config.show_thinking = result.get("show_thinking", self.config.show_thinking)
        self.config.show_tool_output = result.get("show_tool_output", self.config.show_tool_output)
        self.config.browser_headless = result.get("browser_headless", self.config.browser_headless)
        self.config.browser_persistent_profile = result.get(
            "browser_persistent_profile", self.config.browser_persistent_profile)
        self.config.reasoning_effort = result.get("reasoning_effort", self.config.reasoning_effort)
        self.config.chakla_model = result.get("chakla_model", self.config.chakla_model)
        self.config.label_main = result.get("label_main", self.config.label_main)
        self.config.label_worker = result.get("label_worker", self.config.label_worker)

        provider = result.get("provider")
        if provider:
            entry = self.config.providers.get(provider) or ProviderCreds()
            if result.get("api_base") is not None:
                entry.api_base = result["api_base"]
            if result.get("api_key"):
                entry.api_key = result["api_key"]
            if entry.api_key or entry.api_base:
                self.config.providers[provider] = entry

        # Worker may use a different provider than the main model. Ensure that
        # provider has resolvable creds WITHOUT corrupting the main provider's
        # entry: never copy the shared (main) base here — use the worker
        # provider's own default base. Reuse the shared key only if one was
        # entered this session and the worker provider has no key yet.
        from riftor.providers import PROVIDERS as _PROVIDERS  # local: keep import-time light
        w_provider = result.get("chakla_provider")
        if w_provider and w_provider != provider:
            w_entry = self.config.providers.get(w_provider) or ProviderCreds()
            if not w_entry.api_key and result.get("api_key"):
                w_entry.api_key = result["api_key"]
            if not w_entry.api_base:
                w_entry.api_base = _PROVIDERS[w_provider].default_base
            if w_entry.api_key or w_entry.api_base:
                self.config.providers[w_provider] = w_entry

        self.provider = Provider(self.config)
        self.context.lore = self.config.lore
        self.status.set_lore(self.config.lore)
        self.status.set_genz(self.config.genz)
        self.context.genz = self.config.genz
        try:
            self.query_one(Banner).set_genz(self.config.genz)
        except Exception:
            pass
        self.status.set_model(self.config.model)
        self.config.theme = result["theme"]
        self._apply_theme(result["theme"])
        self.config.save()
        self._note("config saved")

    def _permissions_cmd(self, arg: str) -> None:
        parts = arg.split()
        if not parts:
            self._markdown("**permissions**\n\n```\n" + self.permissions.describe() + "\n```")
            return
        sub, rest = parts[0].lower(), parts[1:]
        if sub == "allow" and rest:
            pattern = " ".join(rest[1:]) if len(rest) > 1 else None
            self.permissions.add_allow_rule(rest[0], pattern)
            self._note(f"allow rule added: {rest[0]} {pattern or ''}".strip())
        elif sub == "deny" and rest:
            pattern = " ".join(rest[1:]) if len(rest) > 1 else None
            self.permissions.add_deny_rule(rest[0], pattern)
            self._note(f"deny rule added: {rest[0]} {pattern or ''}".strip())
        else:
            self._note("usage: /permissions [allow <tool> [pattern] | deny <tool> [pattern]]")

    def _set_stage(self, arg: str) -> None:
        if not arg:
            cur = self.engagement.stage
            if self.config.genz:
                names = GENZ_STAGE_NAMES
                letters = GENZ_STAGE_LETTERS
                stages = " · ".join(f"{letters[k]} {v}" for k, v in names.items())
                self._note(f"stage: {letters[cur]} ({names[cur]})   ·   {stages}")
            else:
                stages = " · ".join(f"{k} {v}" for k, v in STAGE_NAMES.items())
                self._note(f"stage: {cur} ({STAGE_NAMES[cur]})   ·   {stages}")
            return
        name_to_letter = {v.lower(): k for k, v in STAGE_NAMES.items()}
        token = arg.strip()
        letter = token.upper() if token.upper() in STAGE_NAMES else name_to_letter.get(token.lower())
        if letter:
            self.engagement.set_stage(letter)
            self._refresh_status()
            self._stage_divider(letter)
        else:
            self._note(f"unknown stage: {arg} — use R/I/F/T or recon/intrusion/foothold/takeover")

    def _stage_divider(self, letter: str) -> None:
        """A scannable, color-coded divider marking a RIFT stage transition."""
        p = self._pal()
        colors = {"R": p["cyan"], "I": p["magenta"], "F": p["violet"], "T": p["danger"]}
        color = colors.get(letter, p["cyan"])
        line = Text()
        line.append("──◢ ", style=color)
        if self.config.genz:
            label = GENZ_STAGE_LETTERS.get(letter, letter)
            name = GENZ_STAGE_NAMES.get(letter, STAGE_NAMES.get(letter, letter))
        else:
            label = letter
            name = STAGE_NAMES.get(letter, letter)
        line.append(f"{label} · {name}", style=f"bold {color}")
        line.append(" ◣" + "─" * 30, style=color)
        self.chat.mount(Static(line, classes="note"))
        self._scroll_if_following()

    def _scope_cmd(self, arg: str) -> None:
        parts = arg.split()
        if not parts:
            none_label = "no glaze yet fr" if self.config.genz else "(none)"
            ins = ", ".join(t.raw for t in self.engagement.scope.in_scope) or none_label
            outs = self.engagement.scope.out_of_scope
            mode = "dry-run" if self.engagement.dry_run else ("on" if self.engagement.enforce else "off")
            line = f"scope · enforce {mode} · in: {ins}"
            if outs:
                line += " · out: " + ", ".join(t.raw for t in outs)
            self._note(line)
            return
        sub, rest = parts[0].lower(), parts[1:]
        if sub in ("add", "in") and rest:
            for target in rest:
                self.engagement.add_scope(target, "in")
            self._note(f"in-scope += {', '.join(rest)}")
        elif sub == "out" and rest:
            for target in rest:
                self.engagement.add_scope(target, "out")
            self._note(f"out-of-scope += {', '.join(rest)}")
        elif sub in ("rm", "remove", "del") and rest:
            for target in rest:
                self.engagement.remove_scope(target)
            self._note(f"removed {', '.join(rest)}")
        elif sub == "clear":
            self.engagement.clear_scope()
            self._note("scope cleared")
        elif sub == "on":
            self.engagement.set_enforce(True)
            self.engagement.set_dry_run(False)
            self._note("scope enforcement ON")
        elif sub == "off":
            self.engagement.set_enforce(False)
            self._error("scope enforcement OFF — riftor will not block out-of-scope actions")
        elif sub in ("dry", "dry-run"):
            self.engagement.set_enforce(True)
            self.engagement.set_dry_run(True)
            self._note("scope dry-run ON — violations are warned but not blocked")
        elif sub == "import" and rest:
            self._scope_import(rest[0])
        elif sub == "export":
            self._scope_export(rest[0] if rest else None)
        else:
            self._note(
                "usage: /scope [add <t>|out <t>|rm <t>|clear|on|off|dry|import <file>|export [file]]"
            )
        self._refresh_status()

    def _scope_import(self, path: str) -> None:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.workdir / p
        try:
            text = p.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._error(f"scope import failed — {exc}")
            return
        added_in, added_out = self.engagement.import_scope(text)
        self._note(f"scope imported: +{added_in} in, +{added_out} out (from {p})")

    def _scope_export(self, path: str | None) -> None:
        out = path or str(self.engagement.dir / "scope.txt")
        p = Path(out).expanduser()
        if not p.is_absolute():
            p = self.workdir / p
        try:
            p.write_text(self.engagement.export_scope(), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._error(f"scope export failed — {exc}")
            return
        self._note(f"scope exported → {p}")

    # ---- findings --------------------------------------------------------------
    def _sorted_findings(self) -> list[dict]:
        from riftor.engagement.report import report_data

        return report_data(self.engagement)["findings"]

    def _show_findings(self) -> None:
        findings = self._sorted_findings()
        if not findings:
            self._note("no findings recorded yet")
            return
        rows = []
        for f in findings:
            host = f" — `{f['host']}`" if f.get("host") else ""
            cvss = f" · CVSS {f['cvss_score']:.1f}" if f.get("cvss_score") else ""
            tags = f"  ‹{f['tags']}›" if f.get("tags") else ""
            rows.append(f"- `#{f['id']}` **[{f['severity'].upper()}{cvss}]** {f['title']}{host}{tags}")
        self._markdown("**findings** (severity-sorted)\n\n" + "\n".join(rows))

    def _show_finding(self, arg: str) -> None:
        try:
            fid = int(arg.strip())
        except ValueError:
            self._note("usage: /finding <id> — see /findings")
            return
        f = self.engagement.store.get_finding(fid)
        if not f:
            self._note(f"no finding #{fid}")
            return
        lines = [f"**#{fid} · [{f['severity'].upper()}] {f['title']}**", ""]
        for label, key in (
            ("Host", "host"), ("CVSS", "cvss"), ("Tags", "tags"),
            ("Evidence", "evidence"), ("Recommendation", "recommendation"), ("Notes", "notes"),
        ):
            if f.get(key):
                lines.append(f"- **{label}:** {f[key]}")
        self._markdown("\n".join(lines))

    def _edit_finding_cmd(self, arg: str) -> None:
        parts = arg.split()
        if not parts:
            self._note("usage: /edit-finding <id> field=value … (sev/severity, host, tags, notes, title)")
            return
        try:
            fid = int(parts[0])
        except ValueError:
            self._note("usage: /edit-finding <id> field=value …")
            return
        alias = {"sev": "severity", "rec": "recommendation"}
        fields: dict[str, str] = {}
        for token in arg.split(maxsplit=1)[1].split() if len(arg.split()) > 1 else []:
            if "=" not in token:
                continue
            key, _, value = token.partition("=")
            key = alias.get(key.lower(), key.lower())
            if key in ("title", "severity", "host", "evidence", "recommendation", "tags", "notes"):
                fields[key] = value
        if not fields:
            self._note("nothing to change — use field=value (e.g. sev=high tags=fp)")
            return
        if self.engagement.store.update_finding(fid, **fields):
            self.engagement.store.log_activity("finding_edit", f"#{fid} {', '.join(fields)}")
            self._note(f"updated finding #{fid}: {', '.join(fields)}")
        else:
            self._note(f"no finding #{fid}")

    def _delete_finding_cmd(self, arg: str) -> None:
        try:
            fid = int(arg.strip())
        except ValueError:
            self._note("usage: /delete-finding <id>")
            return
        if self.engagement.store.delete_finding(fid):
            self.engagement.store.log_activity("finding_delete", f"#{fid}")
            self._refresh_status()
            self._note(f"deleted finding #{fid}")
        else:
            self._note(f"no finding #{fid}")

    def _hosts_cmd(self) -> None:
        hosts = self.engagement.store.list_hosts()
        if not hosts:
            self._note("no hosts recorded yet")
            return
        rows = [f"- `{h['host']}`" + (f" — {h['note']}" if h.get("note") else "") for h in hosts]
        self._markdown("**hosts**\n\n" + "\n".join(rows))

    def _services_cmd(self) -> None:
        services = self.engagement.store.list_services()
        if not services:
            self._note("no services recorded yet")
            return
        rows = ["| Host | Port | Proto | Service | Version |", "| --- | --- | --- | --- | --- |"]
        for s in services:
            rows.append(
                f"| {s.get('host', '')} | {s.get('port') or ''} | {s.get('proto', '')} "
                f"| {s.get('service', '')} | {s.get('version', '')} |"
            )
        self._markdown("**services**\n\n" + "\n".join(rows))

    def _report_cmd(self, arg: str) -> None:
        fmt = (arg or "both").strip().lower()
        if fmt not in ("md", "html", "json", "sarif", "both", "all"):
            self._note("usage: /report [md|html|json|sarif|both|all]")
            return
        try:
            paths = write_reports(self.engagement, fmt)
        except Exception as exc:  # noqa: BLE001
            self._error(f"report failed — {exc}")
            return
        self.engagement.store.log_activity("report", fmt)
        self._note("report written: " + ", ".join(str(p) for p in paths))

    def _timeline_cmd(self) -> None:
        events = self.engagement.store.list_activity(limit=100)
        if not events:
            self._note("no activity recorded yet")
            return
        rows = []
        for e in events:
            when = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
            rows.append(f"- `{when}` **{e['event']}** {e.get('detail', '')}")
        self._markdown("**timeline**\n\n" + "\n".join(rows))

    def _audit_cmd(self) -> None:
        entries = self.audit.tail(30)
        if not entries:
            self._note("no audit entries yet")
            return
        rows = []
        for e in entries:
            when = time.strftime("%H:%M:%S", time.localtime(e.get("ts", 0)))
            flag = "✓" if e.get("allowed") else "✗"
            err = " ⚠" if e.get("is_error") else ""
            rows.append(f"- `{when}` {flag} **{e.get('tool', '?')}** {e.get('preview', '')[:80]}{err}")
        self._markdown("**audit log** (recent)\n\n" + "\n".join(rows))

    def _review_cmd(self) -> None:
        """Self-critique: check all findings for false-positive signals."""
        findings = self.engagement.store.list_findings()
        if not findings:
            self._note("no findings to review")
            return
        lines = ["## Self-Critique Review"]
        issues = 0
        for f in findings:
            fid = f["id"]
            title = f.get("title", "untitled")
            severity = f.get("severity", "info")
            evidence = f.get("evidence", "")
            confidence = f.get("confidence") or 0
            verification = f.get("verification_method", "")
            problems = []
            if not evidence or len(evidence.strip()) < 20:
                problems.append("no/thin evidence")
            if severity in ("high", "critical") and confidence < 7:
                problems.append(f"high severity but low confidence ({confidence})")
            if confidence >= 8 and not verification:
                problems.append("high confidence but no verification method")
            status_only = any(w in (evidence or "").lower()
                              for w in ["status code", "http 500", "http 403", "returned 200"])
            if status_only and len(evidence.strip()) < 100:
                problems.append("evidence looks like status-code-only (not concrete proof)")
            if problems:
                issues += 1
                lines.append(f"- **#{fid}** [{severity}] {title}")
                for p in problems:
                    lines.append(f"  - ⚠ {p}")
            else:
                lines.append(f"- ✅ **#{fid}** [{severity}] {title} — looks solid")
        lines.append(f"\n_{len(findings)} findings reviewed, {issues} with issues_")
        self._markdown("\n".join(lines))

    def _hypotheses_cmd(self) -> None:
        rows = self.engagement.store.list_hypotheses()
        if not rows:
            self._note("no hypotheses recorded. The agent uses record_hypothesis to track leads.")
            return
        lines = ["## Hypotheses"]
        for r in rows:
            status = r.get("status", "open")
            marker = {"open": "🔵", "confirmed": "✅", "refuted": "❌", "inconclusive": "⚪"}.get(status, "?")
            lines.append(f"- {marker} **#{r['id']}** [{status}] {r['statement']}")
            if r.get("rationale"):
                lines.append(f"  - _{r['rationale'][:150]}_")
        open_count = sum(1 for r in rows if r.get("status") == "open")
        lines.append(f"\n_{len(rows)} total, {open_count} open_")
        self._markdown("\n".join(lines))

    def _lesson_cmd(self, arg: str) -> None:
        if not arg.strip():
            self._note("usage: /lesson <text>  — e.g. /lesson WHEN testing JWT → check alg=none first")
            return
        from riftor.engagement.lessons import LessonStore
        text = arg.strip()
        trigger, lesson = "", text
        if "→" in text or "->" in text:
            sep = "→" if "→" in text else "->"
            parts = text.split(sep, 1)
            trigger = parts[0].strip().removeprefix("WHEN").removeprefix("when").strip()
            lesson = parts[1].strip()
        elif text.upper().startswith("WHEN "):
            trigger = text[5:].strip()
            lesson = trigger
        try:
            entry = LessonStore().add(trigger, lesson, source="operator")
            self._note(f"lesson saved: WHEN {entry.trigger} → {entry.lesson}")
        except Exception as e:
            self._note(f"error: {e}")

    def _lessons_cmd(self) -> None:
        from riftor.engagement.lessons import LessonStore
        rows = LessonStore().list()
        if not rows:
            self._note("no lessons saved yet. Use /lesson <text> to teach me.")
            return
        lines = ["## Lessons"]
        for r in rows:
            trigger = r.get("trigger", "")
            lesson = r.get("lesson", "")
            source = r.get("source", "operator")
            if trigger:
                lines.append(f"- **WHEN** {trigger} → {lesson} *({source})*")
            else:
                lines.append(f"- {lesson} *({source})*")
        self._markdown("\n".join(lines))

    def _memory_cmd(self, arg: str) -> None:
        from riftor.engagement.memory import MemoryStore
        store = MemoryStore(self.workdir)
        parts = arg.split(maxsplit=1)
        sub = parts[0].lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        if sub == "add":
            if not rest:
                self._note("usage: /memory add <text>  (or [tag] text)")
                return
            tag = ""
            text = rest
            if rest.startswith("[") and "]" in rest:
                close = rest.index("]")
                tag = rest[1:close].strip()
                text = rest[close + 1:].strip()
            if not text:
                self._note("usage: /memory add <text>  (or [tag] text)")
                return
            try:
                entry = store.add(text, tag, source="operator")
                label = f"[{entry.tag}] {entry.text}" if entry.tag else entry.text
                self._note(f"remembered (#{entry.id}): {label}")
            except Exception as e:
                self._note(f"error: {e}")
            return
        if sub == "rm":
            if not rest:
                self._note("usage: /memory rm <id>")
                return
            if store.remove(rest):
                self._note(f"forgot #{rest}")
            else:
                self._note(f"no memory #{rest}")
            return
        if sub == "clear":
            store.clear()
            self._note("memory cleared")
            return
        rows = store.list()
        if not rows:
            self._note("no memory yet. Use /memory add <text> (or [tag] text).")
            return
        lines = ["## Memory"]
        for r in rows:
            tag = r.get("tag", "")
            text = r.get("text", "")
            rid = r.get("id", "")
            src = r.get("source", "agent")
            prefix = f"[{tag}] " if tag else ""
            lines.append(f"- `{rid}` {prefix}{text} *({src})*")
        self._markdown("\n".join(lines))

    def _template_cmd(self, arg: str) -> None:
        from riftor.engagement.templates import TEMPLATES
        sub = (arg or "").strip()
        if not sub:
            lines = ["## Engagement templates"]
            for key, t in TEMPLATES.items():
                lines.append(f"- `{key}` — {t.description}")
            lines.append("\nApply with `/template <name>` · clear with `/template off`")
            self._markdown("\n".join(lines))
            return
        if sub.lower() == "off":
            self.engagement.set_template("")
            self._note("template cleared")
            self._refresh_status()
            return
        key = sub.lower()
        tmpl = TEMPLATES.get(key)
        if tmpl is None:
            self._note(f"unknown template: {key} — try /template for the list")
            return
        self.engagement.set_stage(tmpl.stage)
        self.engagement.set_template(key)
        self._refresh_status()
        tools = ", ".join(tmpl.tools)
        self._markdown(
            f"**template applied: {tmpl.label}**  ·  stage → {tmpl.stage}\n\n"
            f"suggested tools: {tools}\n\n{tmpl.methodology}"
        )

    def _doctor_cmd(self) -> None:
        from riftor.engagement.doctor import check_toolchain, render_markdown

        self._markdown(render_markdown(check_toolchain()))

    def _browser_cmd(self, arg: str) -> None:
        sub = (arg or "").strip().lower()
        mgr = self.toolctx.browser
        if sub in ("headed", "headless"):
            self.config.browser_headless = sub == "headless"
            self.config.save()
            self._note(f"browser mode → {sub} (applies on next launch)")
            return
        if sub == "close":
            if mgr is not None and mgr.launched:
                self.run_worker(mgr.close(), exclusive=False, exit_on_error=False)
                self._note("closing browser…")
            else:
                self._note("no browser running")
            return
        mode = "headless" if self.config.browser_headless else "headed"
        profile = "persistent" if self.config.browser_persistent_profile else "incognito"
        state = "running" if (mgr and mgr.launched) else "not launched"
        self._note(f"browser: {state} · {mode} · {profile} (toggle in /config)")

    @work(group="screenshots")
    async def _screenshots_cmd(self) -> None:
        await self.push_screen_wait(ScreenshotGalleryScreen(self.workdir))

    def _export_cmd(self) -> None:
        import json
        import shutil

        out_dir = self.engagement.dir / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        stage = out_dir / f"engagement-{stamp}"
        stage.mkdir(exist_ok=True)
        try:
            (stage / "engagement.json").write_text(self.engagement.store.dump_json(), encoding="utf-8")
            db = self.engagement.dir / "engagement.db"
            if db.exists():
                shutil.copy2(db, stage / "engagement.db")
            manifest = {
                "tool": "riftor",
                "exported": stamp,
                "stage": self.engagement.stage,
                "findings": self.engagement.findings_count(),
                "model": self.config.model,
            }
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            archive = shutil.make_archive(str(stage), "zip", root_dir=str(stage))
            shutil.rmtree(stage, ignore_errors=True)
        except Exception as exc:  # noqa: BLE001
            self._error(f"export failed — {exc}")
            return
        self.engagement.store.log_activity("export", archive)
        self._note(f"engagement exported → {archive}")

    def _conversation_cmd(self) -> None:
        """Export the full conversation as markdown to .riftor/reports/."""
        import json
        import textwrap

        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        out_dir = self.engagement.dir / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"conversation_{self.session_id}.md"
        path = out_dir / fname

        lines = [
            "# riftor · Conversation Export",
            "",
            f"**Session:** `{self.session_id}`",
            f"**Model:** `{self.config.model}`",
            f"**Exported:** {stamp}",
            f"**Messages:** {len(self.context.messages)}",
            "",
            "---",
            "",
        ]

        for msg in self.context.messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                continue  # skip the system prompt

            elif role == "user":
                lines.append("## User")
                lines.append("")
                lines.append(content if isinstance(content, str) else str(content))
                lines.append("")

            elif role == "assistant":
                if isinstance(content, str) and content:
                    lines.append("## Assistant")
                    lines.append("")
                    lines.append(content)
                    lines.append("")

                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    name = fn.get("name", "?")
                    raw_args = fn.get("arguments", "{}")
                    # indent the json one level so it sits nicely under the heading
                    try:
                        args_obj = json.loads(raw_args)
                        args_fmt = json.dumps(args_obj, indent=2)
                    except (json.JSONDecodeError, TypeError):
                        args_fmt = str(raw_args)
                    args_indented = textwrap.indent(args_fmt, "    ")
                    lines.append(f"### `{name}`")
                    lines.append("")
                    lines.append("```json")
                    lines.append(args_indented)
                    lines.append("```")
                    lines.append("")

            elif role == "tool":
                call_id = msg.get("tool_call_id", "?")
                lines.append(f"### Tool Result (`{call_id}`)")
                lines.append("")
                text = content if isinstance(content, str) else str(content)
                if len(text) > 8000:
                    text = text[:8000] + "\n\n… (truncated)"
                lines.append("```")
                lines.append(text)
                lines.append("```")
                lines.append("")

        try:
            path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self._error(f"conversation export failed — {exc}")
            return
        self._note(f"conversation exported → {path}")

    # ---- conversation utilities ------------------------------------------------
    def _cost_cmd(self) -> None:
        u = self.usage
        cost = f" · ${u.cost:.4f}" if u.cost else ""
        pct = int(self.context.estimated_tokens() / self._context_window() * 100)
        self._note(
            f"session usage: {u.prompt_tokens} in + {u.completion_tokens} out = "
            f"{u.total_tokens} tokens{cost} · context ~{pct}% of window"
        )

    def _retry_cmd(self) -> None:
        last = self.context.pop_last_user_turn()
        if last is None:
            self._note("nothing to retry")
            return
        self._note("retrying last turn…")
        self._add_user(last)
        self._agent(last)

    def _continue_cmd(self, arg: str) -> None:
        extra = 16
        if arg.strip().isdigit():
            extra = int(arg.strip())
        self._note(f"continuing — extending the step budget by {extra}")
        self._agent("Continue from where you left off.", extra_steps=extra)

    def _compact_cmd(self) -> None:
        changed = self.context.compact()
        self._refresh_usage()
        self._note(f"compacted {changed} old tool result(s) to free context")

    def _copy_cmd(self) -> None:
        if not self._last_output:
            self._note("nothing to copy yet")
            return
        try:
            self.copy_to_clipboard(self._last_output)
            self._note(f"copied {len(self._last_output)} chars to clipboard")
        except Exception as exc:  # noqa: BLE001
            self._error(f"copy failed — {exc}")

    def _show_result(self, arg: str) -> None:
        try:
            rid = int(arg.strip())
        except ValueError:
            ids = ", ".join(f"#{i}" for i in sorted(self._tool_results)) or "(none)"
            self._note(f"usage: /show <id> — available: {ids}")
            return
        content = self._tool_results.get(rid)
        if content is None:
            self._note(f"no stored result #{rid}")
            return
        self.chat.mount(Static(Text(content), classes="tool-result"))
        self._last_output = content
        self._scroll_if_following()

    # ---- sessions --------------------------------------------------------------
    def _save_session(self, complete: bool = True) -> None:
        try:
            sessions.save(
                self.workdir, self.session_id, self.context.dump(), self.config.model,
                complete=complete,
            )
        except Exception:  # noqa: BLE001 — persistence must never crash the app
            pass

    def _sessions_cmd(self) -> None:
        rows = sessions.list_sessions(self.workdir)
        if not rows:
            self._note("no saved sessions")
            return
        lines = []
        for s in rows:
            marker = "→ " if s["id"] == self.session_id else ""
            flag = "" if s.get("complete", True) else " ⚠"
            lines.append(f"- {marker}`{s['id']}`{flag} · {s['messages']} msgs · {s['title']}")
        self._markdown("**sessions**\n\n" + "\n".join(lines))

    def _resume_cmd(self, arg: str) -> None:
        sid = arg.strip()
        if not sid:
            self._note("usage: /resume <id> — see /sessions")
            return
        data = sessions.load(self.workdir, sid)
        if not data:
            self._note(f"no such session: {sid}")
            return
        self._reset_browser_for_session()
        self.session_id = data["id"]
        self.context.load(data.get("messages", []))
        self.context.repair()
        self._clear_flock()
        self.chat.remove_children()
        self._replay_transcript(self.context.messages)
        self._note(f"resumed session {sid} ({len(data.get('messages', []))} messages)")

    def _reset_browser_for_session(self) -> None:
        """Close and forget the session's browser on a session switch (/new, /resume)
        so a new session starts with no carried-over page/cookies, and the first-run
        incognito hint fires again."""
        mgr = self.toolctx.browser
        if mgr is not None and mgr.launched:
            self.run_worker(mgr.close(), exclusive=False, exit_on_error=False)
        self.toolctx.browser = None
        self._browser_hint_shown = False

    async def _offer_recovery(self) -> None:
        """Prompt the operator to resume a crashed (incomplete) session."""
        incomplete = sessions.find_incomplete(self.workdir)
        if not incomplete:
            return
        crashed = incomplete[0]  # newest incomplete
        resume = await self.push_screen_wait(
            _RecoveryModal(crashed["id"], crashed.get("title", ""))
        )
        if resume:
            self._resume_cmd(crashed["id"])
        else:
            self._note(
                "rift online · set scope with /scope add <target> before tasking the agent"
            )

    def _new_session(self) -> None:
        self._save_session()
        self._reset_browser_for_session()
        self.session_id = sessions.new_id()
        self.context.clear()
        self.usage = Usage()
        self.chakla_usage = Usage()  # reset the 🐦 worker gauge with the session
        self._refresh_usage()
        self._clear_flock()
        self.chat.remove_children()
        self._note(f"new session {self.session_id}")

    def _replay_transcript(self, messages: list[dict]) -> None:
        """Re-render a saved conversation (compact) so the screen reflects history."""
        p = self._pal()
        for msg in messages:
            role = msg.get("role")
            if role == "user":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    self.chat.mount(Static(Text(content), classes="user"))
            elif role == "assistant":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    self.chat.mount(Markdown(content, classes="assistant"))
                for call in msg.get("tool_calls") or []:
                    name = call.get("function", {}).get("name", "tool")
                    self.chat.mount(Static(Text(f"⛏ {name}", style=p["cyan"]), classes="tool"))
            elif role == "tool":
                content = str(msg.get("content", ""))
                first = content.splitlines()[0] if content else ""
                self.chat.mount(Static(Text(first[:200], style=p["dim"]), classes="tool-result"))
        self.chat.scroll_end(animate=False)

    def action_clear(self) -> None:
        self.context.clear()
        self.usage = Usage()
        self.chakla_usage = Usage()  # reset the 🐦 worker gauge with the session
        self._refresh_usage()
        self._clear_flock()
        self.chat.remove_children()
        self._note("conversation cleared")

    def action_quit(self) -> None:
        self._save_session()
        self.exit()

    def action_cancel(self) -> None:
        self.workers.cancel_all()
        self._close_modals()
        self.status.set_busy(False)
        self._note("cancelled")

    def _close_modals(self) -> None:
        """Pop any permission modal left over from a cancelled worker."""
        while isinstance(self.screen, ConfirmScreen):
            self.pop_screen()

    # ---- rate limiting ---------------------------------------------------------
    async def _rate_gate(self) -> None:
        limit = self.config.rate_limit_per_min
        if limit <= 0:
            return
        import asyncio

        now = time.monotonic()
        self._rate_times = [t for t in self._rate_times if now - t < 60.0]
        if len(self._rate_times) >= limit:
            wait = 60.0 - (now - self._rate_times[0])
            if wait > 0:
                self._note(f"rate limit ({limit}/min) — waiting {wait:.0f}s")
                await asyncio.sleep(wait)
        self._rate_times.append(time.monotonic())

    # ---- agent loop ------------------------------------------------------------
    @work(exclusive=True)
    async def _agent(self, user_text: str, extra_steps: int = 0) -> None:
        self._close_modals()  # a prior run may have been cancelled mid-prompt
        self._autoscroll = True
        if user_text != "Continue from where you left off.":
            self.context.add_user(user_text)
            self._last_user_text = user_text
        self.status.set_busy(True)
        # chat-area pulse spinner
        label = "Baaj is cooking…" if self.config.genz else "opening rift…"
        self._spinner = PulseSpinner(label, classes="spinner")
        await self._mount(self._spinner)
        self._spinner.start()
        budget = 10**9 if self.yolo else self.max_steps + extra_steps
        _recent_cmds: list[str] = []
        _barren_rounds = 0
        _findings_before = self.engagement.findings_count() if self.engagement else 0
        try:
            for step in range(budget):
                self._save_session(complete=False)  # crash-safe checkpoint
                await self._rate_gate()
                turn = await self._assistant_turn()
                self.context.add_message(turn.assistant_message)
                self.usage.add(turn.usage)
                self._refresh_usage()
                pct = int(self.context.estimated_tokens() / self._context_window() * 100)
                if pct >= 80:
                    self._note(f"⚠ context ~{pct}% of window — /compact or /clear soon")
                if not turn.tool_calls:
                    if not turn.text.strip():
                        self._note("(no output)")
                    break
                # anti-loop: detect repeated tool calls. Each call gets exactly
                # one tool result — either from _run_tool or a synthetic one here,
                # never both, or the turn becomes malformed (duplicate tool_call_id).
                anti_loop_stop = False
                for call in turn.tool_calls:
                    if anti_loop_stop:
                        # A prior call this turn tripped the hard stop. Don't run the
                        # rest, but still answer every tool_call id so none are left
                        # orphaned (an orphan only gets repaired on the next turn).
                        self.context.add_tool_result(
                            call.id, "[anti-loop] skipped — stopping this turn."
                        )
                        continue
                    sig = antiloop.call_signature(call.name, call.arguments)
                    decision = antiloop.classify(_recent_cmds, sig)
                    if decision.stop:
                        # Hard stop: inject this call's single result and skip running
                        # it. Remaining calls are answered by the guard above.
                        self._note(
                            "⚠ anti-loop: same tool call repeated "
                            f"{antiloop.STOP_AT}+ times — stopping. Try a different approach."
                        )
                        self.context.add_tool_result(
                            call.id,
                            f"[anti-loop] You have repeated this exact call {decision.repeat_count} "
                            "times. STOP and try a completely different approach.",
                        )
                        anti_loop_stop = True
                        continue
                    if decision.warn:
                        # Operator-only notice; still run the tool so the call gets
                        # its one real result. No second tool message.
                        self._note(
                            f"⚠ anti-loop: same tool call repeated {decision.repeat_count}× — "
                            "nudging the agent to change approach."
                        )
                    await self._run_tool(call)
                if anti_loop_stop:
                    break
                # all tools ran normally; check barren rounds
                current_findings = self.engagement.findings_count() if self.engagement else 0
                if current_findings > _findings_before:
                    _barren_rounds = 0
                    _findings_before = current_findings
                else:
                    _barren_rounds += 1
                if _barren_rounds >= 8:
                    self._note(
                        "⚠ circuit breaker: 8 rounds with no new findings — "
                        "stopping. /continue to resume or change approach."
                    )
                    break
            else:
                self._note(
                    f"reached step limit ({budget}); stopping — /continue to extend"
                )
        except ProviderError as exc:
            self._error(f"rift collapsed [{exc.kind}] — {exc}")
        except Exception as exc:  # noqa: BLE001
            self._error(f"rift collapsed — {exc}")
        finally:
            self._clear_spinner()
            self._clear_flock()
            self.status.set_busy(False)
            self.chat.scroll_end(animate=False)
            self._save_session(complete=True)

    async def _assistant_turn(self) -> Turn:
        self.context.repair()

        p = self._pal()
        thinking_block: Static | None = None
        thinking_buf: list[str] = []
        bubble: Markdown | None = None
        buffer: list[str] = []
        last_render = 0.0
        last_think_render = 0.0
        turn: Turn | None = None

        async for event, payload in self.provider.stream_turn(
            self.context.messages, self.tool_schemas
        ):
            if event == "thinking":
                if not self.config.show_thinking:
                    continue
                thinking_buf.append(str(payload))
                if thinking_block is None:
                    thinking_block = Static(Text(""), classes="thinking")
                    await self._mount(thinking_block)
                now = time.monotonic()
                if now - last_think_render > 0.08:
                    thinking_block.update(
                        Text("💭 " + "".join(thinking_buf), style=f"italic {p['dim']}")
                    )
                    self._scroll_if_following()
                    last_think_render = now
            elif event == "text":
                buffer.append(str(payload))
                if bubble is None:
                    bubble = Markdown("", classes="assistant")
                    await self._mount(bubble)
                now = time.monotonic()
                if now - last_render > 0.08:
                    await bubble.update("".join(buffer))
                    self._scroll_if_following()
                    last_render = now
            elif event == "done":
                turn = payload  # type: ignore[assignment]

        # finalize the thinking block (flush any buffered tail)
        if thinking_block is not None:
            thinking_block.update(
                Text("💭 " + "".join(thinking_buf), style=f"italic {p['dim']}")
            )

        text = "".join(buffer).strip()
        if text:
            if bubble is None:
                bubble = Markdown("", classes="assistant")
                await self._mount(bubble)
            await bubble.update(text)
            self._last_output = text
        elif bubble is not None:
            await bubble.remove()
        self._scroll_if_following()
        return turn or Turn(text=text, assistant_message={"role": "assistant", "content": text})

    async def _run_tool(self, call: ToolCall) -> None:
        tool = tools.get(call.name)
        if tool is None:
            await self._show_tool_call(call.name, "unknown tool", danger=True)
            msg = f"error: unknown tool '{call.name}'"
            await self._show_tool_result(msg, is_error=True)
            self.context.add_tool_result(call.id, msg)
            return

        preview = tool.preview(call.arguments)
        await self._show_tool_call(tool.name, preview, danger=tool.danger)

        # scope enforcement for target-touching tools (bash, webfetch)
        scope_warning: list[str] = []
        if not self.yolo and getattr(tool, "scope_sensitive", False):
            probe = " ".join(str(v) for v in call.arguments.values())
            scope_warning = self.engagement.violations(probe)

        # dry-run: warn but don't block
        if not self.yolo and scope_warning and self.engagement.dry_run:
            self._note(f"⚠ dry-run: out of scope — {', '.join(scope_warning)} (allowed)")
            scope_warning = []

        # hard deny rules (e.g. bash rm -rf) — refuse without prompting
        if not self.yolo and self.permissions.is_denied(tool.name, preview):
            reason = "blocked by a deny rule"
            await self._show_tool_result(reason, is_error=True)
            self.context.add_tool_result(
                call.id,
                "[blocked by policy] This action matches a deny rule and was refused. "
                "Do not retry it; propose a safer alternative.",
            )
            self.audit.record(tool.name, preview, allowed=False)
            return

        if not self.yolo and (scope_warning or self.permissions.needs_prompt(
            tool.name, tool.requires_permission, preview
        )):
            detail = tool.confirm_detail(call.arguments, self.toolctx)
            decision = await self.push_screen_wait(
                ConfirmScreen(tool.name, preview, scope_warning=scope_warning, detail=detail)
            )
            if decision in ("deny", "always_deny"):
                if decision == "always_deny":
                    self.permissions.add_deny_rule(tool.name)
                reason = "blocked — out of scope" if scope_warning else "denied by operator"
                await self._show_tool_result(reason, is_error=True)
                if scope_warning:
                    hint = (
                        f"[blocked: out of scope] {', '.join(scope_warning)} not in scope. "
                        "Do not attempt these targets; work only within the defined scope."
                    )
                else:
                    hint = (
                        "[denied by operator] The action was refused. Do not retry it; "
                        "explain the limitation or propose a safer, in-scope alternative."
                    )
                self.context.add_tool_result(call.id, hint)
                self.audit.record(tool.name, preview, allowed=False)
                return
            if decision == "session":
                self.permissions.allow_for_session(tool.name)
            elif decision == "always":
                self.permissions.add_allow_rule(tool.name)
            # "once" → approve this single call, remember nothing

        audit_preview = preview + (" [scope-override]" if scope_warning else "")
        if call.name.startswith("browser_") and not self._browser_hint_shown:
            self._browser_hint_shown = True
            if not self.config.browser_persistent_profile:
                self._note(
                    "browser running in incognito (nothing saved) · enable persistent "
                    "profile in /config to keep cookies/logins across runs"
                )
        start = time.monotonic()
        try:
            result = await tool.execute(call.arguments, self.toolctx)
        except Exception as exc:  # noqa: BLE001
            result = ToolResult(f"error: {exc}", is_error=True)
        if call.name == "dispatch_chakla":
            self._clear_flock()
        result = result.truncated(self.config.max_result_chars)
        duration = time.monotonic() - start

        self.audit.record(
            tool.name,
            audit_preview,
            allowed=True,
            is_error=result.is_error,
            duration=duration,
            result_len=len(result.content),
        )
        await self._show_tool_result(result.content, is_error=result.is_error)
        if call.name == "browser_screenshot" and not result.is_error:
            import re

            m = re.search(r"saved → (\S+\.png)", result.content)
            if m:
                shot = Path(m.group(1))
                if shot.exists():
                    rendered_inline = False
                    if _InlineImage is not None:
                        try:
                            await self._mount(_InlineImage(str(shot)))
                            rendered_inline = True
                        except Exception:  # noqa: BLE001 — terminal can't render; fall back
                            rendered_inline = False
                    if not rendered_inline:
                        # OSC-8 clickable hyperlink to the PNG (works in modern
                        # terminals; the plain path is also shown by the result line).
                        uri = shot.resolve().as_uri()
                        link = Text("open screenshot", style=f"underline {self._pal()['cyan']}")
                        link.stylize(f"link {uri}")
                        await self._mount(Static(link, classes="note"))
        self.context.add_tool_result(call.id, result.content)
        self._last_output = result.content
        self._refresh_status()

    # ---- tool rendering --------------------------------------------------------
    async def _show_tool_call(self, name: str, preview: str, danger: bool = False) -> None:
        p = self._pal()
        line = Text()
        line.append("⛏ ", style=p["cyan"])
        line.append(name, style=f"bold {p['magenta']}" if danger else f"bold {p['cyan']}")
        if preview:
            line.append("  ")
            line.append(preview, style=p["muted"])
        await self._mount(Static(line, classes="tool"))

    async def _show_tool_result(self, content: str, is_error: bool = False) -> None:
        max_lines = self.config.result_preview_lines
        lines = content.splitlines() or [""]
        if not self.config.show_tool_output:
            # Hidden by /config: don't render, but still register the full result
            # so the operator can reveal it on demand with /show N. The call line
            # (⛏ toolname …) is mounted separately and stays visible.
            rid = len(self._tool_results) + 1
            self._tool_results[rid] = content
            return
        shown = "\n".join(lines[:max_lines])
        if len(lines) > max_lines:
            rid = len(self._tool_results) + 1
            self._tool_results[rid] = content
            shown += f"\n…(+{len(lines) - max_lines} more lines · /show {rid})"
        classes = "tool-result error" if is_error else "tool-result"
        await self._mount(Static(Text(shown), classes=classes))
