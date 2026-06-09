"""Permission state + the confirmation modal for dangerous tool calls.

Permissions are layered, in priority order:

1. **deny rules** — hard block (e.g. ``bash rm -rf``); never even prompts.
2. **allow rules** — auto-approve a tool, or a tool whose preview matches a pattern.
3. **session grants** — "allow for session" choices made this run.
4. otherwise → prompt the operator (once / session / always-allow / deny / always-deny).

Persistent rules live in ``~/.config/riftor/permissions.toml`` so an operator's
trust choices survive across runs. "Allow once" approves exactly this call
without remembering anything.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


def _default_deny() -> list[dict]:
    """Sensible destructive-command guards, on by default for the bash tool."""
    return [
        {"tool": "bash", "pattern": r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r"},
        {"tool": "bash", "pattern": r"\bdd\s+.*of=/dev/"},
        {"tool": "bash", "pattern": r"\bmkfs(\.\w+)?\b"},
        {"tool": "bash", "pattern": r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&"},  # fork bomb
        {"tool": "bash", "pattern": r">\s*/dev/sd[a-z]"},
    ]


class Rule:
    """A tool/pattern matcher. ``pattern`` matches against the call preview."""

    def __init__(self, tool: str = "*", pattern: str | None = None) -> None:
        self.tool = tool or "*"
        self.pattern = pattern
        self._rx = re.compile(pattern, re.IGNORECASE) if pattern else None

    def matches(self, tool_name: str, preview: str) -> bool:
        if self.tool not in ("*", tool_name):
            return False
        if self._rx is None:
            return True
        return bool(self._rx.search(preview or ""))


class Permissions:
    """Layered permission engine: persistent rules + per-session grants."""

    def __init__(
        self,
        allow: list[dict] | None = None,
        deny: list[dict] | None = None,
    ) -> None:
        self.allow_rules = [Rule(**r) for r in (allow or [])]
        self.deny_rules = [Rule(**r) for r in (deny if deny is not None else _default_deny())]
        self.session_allowed: set[str] = set()
        self.session_denied: set[str] = set()
        self._path: Path | None = None

    # -- persistence ------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "Permissions":
        perms = cls()
        perms._path = path
        if not path.exists():
            return perms
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except Exception:  # noqa: BLE001 — bad config must never crash the app
            return perms
        section = data.get("permissions", data)
        allow = section.get("allow") or []
        deny = section.get("deny")
        perms.allow_rules = [Rule(**r) for r in allow]
        perms.deny_rules = [Rule(**r) for r in (deny if deny is not None else [])]
        if deny is None:  # no deny key at all → keep the safe defaults
            perms.deny_rules = [Rule(**r) for r in _default_deny()]
        return perms

    def save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        def rule_line(r: Rule) -> str:
            # TOML *literal* strings (single quotes) so regex backslashes survive
            # without escaping. Single quotes inside a pattern are rare; strip if any.
            if r.pattern:
                pat = f", pattern = '{r.pattern.replace(chr(39), '')}'"
            else:
                pat = ""
            return f'  {{ tool = "{r.tool}"{pat} }},'

        lines = ["# riftor permissions", "", "[permissions]", ""]
        lines.append("# allow = auto-approve; deny = hard-block (never prompts).")
        lines.append("allow = [")
        lines += [rule_line(r) for r in self.allow_rules]
        lines.append("]")
        lines.append("deny = [")
        lines += [rule_line(r) for r in self.deny_rules]
        lines.append("]")
        fd = os.open(str(self._path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, ("\n".join(lines) + "\n").encode("utf-8"))
        finally:
            os.close(fd)

    # -- decisions --------------------------------------------------------------
    def is_denied(self, tool_name: str, preview: str = "") -> bool:
        if tool_name in self.session_denied:
            return True
        return any(r.matches(tool_name, preview) for r in self.deny_rules)

    def is_allowed(self, tool_name: str, preview: str = "") -> bool:
        if tool_name in self.session_allowed:
            return True
        return any(r.matches(tool_name, preview) for r in self.allow_rules)

    def needs_prompt(self, tool_name: str, requires_permission: bool, preview: str = "") -> bool:
        if not requires_permission:
            return False
        if self.is_allowed(tool_name, preview):
            return False
        return True

    def without_session_grants(self) -> "Permissions":
        """A view safe to hand to subagents: standing allow/deny rules and
        session *denials* still bind, but the operator's interactive
        'allow for session' grants do NOT carry over (workers have no operator).
        """
        view = Permissions.__new__(Permissions)
        view.allow_rules = self.allow_rules
        view.deny_rules = self.deny_rules
        view.session_allowed = set()  # the whole point: do not inherit session allows
        view.session_denied = set(self.session_denied)
        view._path = None
        return view

    def allow_for_session(self, tool_name: str) -> None:
        self.session_allowed.add(tool_name)

    def deny_for_session(self, tool_name: str) -> None:
        self.session_denied.add(tool_name)

    def add_allow_rule(self, tool: str, pattern: str | None = None) -> None:
        self.allow_rules.append(Rule(tool, pattern))
        self.save()

    def add_deny_rule(self, tool: str, pattern: str | None = None) -> None:
        self.deny_rules.append(Rule(tool, pattern))
        self.save()

    def describe(self) -> str:
        def fmt(rules: list[Rule]) -> str:
            if not rules:
                return "(none)"
            return ", ".join(f"{r.tool}{':' + r.pattern if r.pattern else ''}" for r in rules)

        return (
            f"allow: {fmt(self.allow_rules)}\n"
            f"deny: {fmt(self.deny_rules)}\n"
            f"session-allow: {', '.join(sorted(self.session_allowed)) or '(none)'}"
        )


class ConfirmScreen(ModalScreen[str]):
    """Asks the operator to approve a tool call.

    Dismisses with one of: ``once`` (this call only), ``session`` (rest of run),
    ``always`` (persist an allow rule), ``deny`` (refuse), ``always_deny``
    (persist a deny rule). ``detail`` is an optional multi-line preview (e.g. a diff).
    """

    BINDINGS = [
        ("escape", "decide('deny')", "Deny"),
        ("a", "decide('once')", "Allow once"),
        ("s", "decide('session')", "Allow session"),
        ("w", "decide('always')", "Always allow"),
        ("d", "decide('always_deny')", "Always deny"),
    ]

    def __init__(
        self,
        tool_name: str,
        preview: str,
        scope_warning: list[str] | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.preview = preview
        self.scope_warning = scope_warning or []
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            if self.scope_warning:
                title = f"⚠ OUT OF SCOPE  ·  {self.tool_name}"
                title_style = "bold #fca5a5"
            else:
                title = f"permission required  ·  {self.tool_name}"
                title_style = "bold #f0abfc"
            yield Static(Text(title, style=title_style), id="confirm-title")
            if self.scope_warning:
                yield Static(
                    Text("not in scope: " + ", ".join(self.scope_warning), style="bold #fca5a5"),
                    id="confirm-scope",
                )
            yield Static(Text(self.preview or "(no detail)", style="#e9e9f2"), id="confirm-detail")
            if self.detail:
                with VerticalScroll(id="confirm-diff"):
                    yield Static(self._render_detail(), id="confirm-diff-body")
            with Horizontal(id="confirm-buttons"):
                yield Button("Once (a)", id="once", variant="success")
                yield Button("Session (s)", id="session", variant="primary")
                yield Button("Always (w)", id="always", variant="primary")
                yield Button("Deny (esc)", id="deny", variant="error")
                yield Button("Never (d)", id="always_deny", variant="error")

    def _render_detail(self) -> Text:
        """Color a unified diff (or show plain preview text)."""
        text = Text()
        for line in (self.detail or "").splitlines()[:200]:
            if line.startswith("+") and not line.startswith("+++"):
                text.append(line + "\n", style="#86efac")
            elif line.startswith("-") and not line.startswith("---"):
                text.append(line + "\n", style="#fca5a5")
            elif line.startswith("@@"):
                text.append(line + "\n", style="#22d3ee")
            else:
                text.append(line + "\n", style="#8b8ba7")
        return text

    def on_mount(self) -> None:
        self.query_one("#once", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")

    def action_decide(self, choice: str) -> None:
        self.dismiss(choice)
