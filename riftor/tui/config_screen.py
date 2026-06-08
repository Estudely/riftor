"""The /config settings modal — a grouped, aligned settings card."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Switch

from riftor.providers import (
    PROVIDER_DEFAULTS,
    PROVIDERS,
    FetchResult,
    apply_prefix,
    fetch_models,
    provider_key_for_model,
)
from riftor.codex_auth import auth_status
from riftor.config import REASONING_EFFORTS
from riftor.tui.theme import THEMES

if TYPE_CHECKING:
    from riftor.config import Config
    from riftor.tui.app import RiftorApp


def _row(label: str, field: Widget) -> Horizontal:
    """A label-column + field row, so every field's left edge lines up."""
    return Horizontal(Label(label, classes="field-label"), field, classes="field-row")


# The five config sections, in display order. (key, nav-label) — the key is
# used for the panel id (#section-<key>) and show_section(); the label is what
# the left nav shows. Glyphs are cosmetic and theme-neutral.
SECTIONS: list[tuple[str, str]] = [
    ("model", "◆ Model"),
    ("generation", "∿ Generation"),
    ("workers", "🐦 Workers"),
    ("appearance", "✦ Appearance"),
    ("display", "▤ Display"),
]


def _model_options(provider_key: str) -> list[tuple[str, str]]:
    return [(m, m) for m in PROVIDER_DEFAULTS.get(provider_key, [])]


class ConfigScreen(ModalScreen[dict | None]):
    """Edit runtime settings. Dismisses with a dict of changes, or None on cancel."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: "Config") -> None:
        super().__init__()
        self.config = config
        self._original_theme = config.theme
        self._provider = provider_key_for_model(config.model)
        self._provider_initialized = False
        # Worker (Chakla) picker mirrors the main one. Empty chakla_model =>
        # reuse main model, so seed the worker provider from the main model then.
        self._worker_provider = provider_key_for_model(
            self.config.chakla_model or self.config.model)
        self._worker_provider_initialized = False

    def compose(self) -> ComposeResult:
        theme = self.config.theme if self.config.theme in THEMES else "rift"
        pkey = self._provider
        meta = PROVIDERS[pkey]
        bare = (self.config.model[len(meta.prefix):]
                if meta.prefix and pkey != "openrouter"
                and self.config.model.startswith(meta.prefix)
                else self.config.model)
        saved = self.config.providers.get(pkey)
        base_val = (saved.api_base if saved else None) or meta.default_base or ""
        model_opts = _model_options(pkey)
        # Guarantee the configured model is a selectable option even when it is
        # not in PROVIDER_DEFAULTS (e.g. an older or custom id). This keeps
        # ``model_val`` always legal — Select.BLANK does not exist in Textual
        # 8.x and would crash on mount with InvalidSelectValueError.
        if bare and bare not in [v for _, v in model_opts]:
            model_opts = [(bare, bare), *model_opts]
        model_val = bare if bare else Select.NULL
        # --- worker (Chakla) picker display values, mirroring the main model ---
        wkey = self._worker_provider
        wmeta = PROVIDERS[wkey]
        wsrc = self.config.chakla_model or self.config.model
        wbare = (wsrc[len(wmeta.prefix):]
                 if wmeta.prefix and wkey != "openrouter"
                 and wsrc.startswith(wmeta.prefix)
                 else wsrc)
        w_model_opts = _model_options(wkey)
        if wbare and wbare not in [v for _, v in w_model_opts]:
            w_model_opts = [(wbare, wbare), *w_model_opts]
        # Empty chakla_model => "reuse main" => show nothing selected.
        w_model_val = wbare if self.config.chakla_model and wbare else Select.NULL
        with Vertical(id="config-box"):
            yield Label("riftor · config", id="config-title")
            with Horizontal(id="config-main"):
                # left nav — keyboard-focusable; selection drives show_section()
                # NOTE: items are passed as constructor children, not via
                # ListView.extend(), because extend() calls mount() internally
                # which raises MountError during compose() (the ListView is not
                # mounted yet). Constructor children are queued as pending and
                # mounted with the parent — the supported compose-time pattern.
                yield ListView(
                    *(ListItem(Label(label), id=f"nav-{key}") for key, label in SECTIONS),
                    id="config-nav",
                )
                # right content area — all five panels mounted; non-active hidden
                with VerticalScroll(id="config-pane"):
                    with Vertical(id="section-model", classes="config-section-panel"):
                        yield Label("Model", classes="config-section")
                        yield _row("Provider", Select(
                            [(m.label, k) for k, m in PROVIDERS.items()],
                            value=pkey, allow_blank=False, id="cfg-provider"))
                        yield _row("Model", Select(
                            model_opts, value=model_val, allow_blank=True, id="cfg-model-select"))
                        yield _row("Custom id", Input(
                            value="", placeholder="override (optional)", id="cfg-model"))
                        yield _row("Base URL", Input(
                            value=base_val, placeholder="provider default", id="cfg-base"))
                        yield _row("API key", Input(
                            password=True, placeholder="leave blank to keep", id="cfg-key"))
                        yield _row("Codex login", Label(
                            self._codex_status_text(), id="cfg-codex-status"))
                        with Horizontal(classes="field-row"):
                            yield Label("", classes="field-label")
                            yield Button("Fetch models", id="cfg-fetch", variant="primary")

                    with Vertical(id="section-generation", classes="config-section-panel hidden"):
                        yield Label("Generation", classes="config-section")
                        yield _row("Temperature", Input(value=str(self.config.temperature), id="cfg-temp"))
                        yield _row("Max tokens", Input(value=str(self.config.max_tokens), id="cfg-maxtok"))
                        yield _row("Tool call steps", Input(value=str(self.config.max_steps), id="cfg-maxsteps"))

                    with Vertical(id="section-workers", classes="config-section-panel hidden"):
                        yield Label("Workers", classes="config-section")
                        yield _row("Provider", Select(
                            [(m.label, k) for k, m in PROVIDERS.items()],
                            value=wkey, allow_blank=False, id="cfg-chakla-provider"))
                        yield _row("Model", Select(
                            w_model_opts, value=w_model_val, allow_blank=True,
                            id="cfg-chakla-model-select"))
                        yield _row("Custom id", Input(
                            value="", placeholder="blank = reuse main model",
                            id="cfg-chakla-custom"))
                        yield _row("Main label", Input(
                            value=self.config.label_main, placeholder="e.g. Baaj",
                            id="cfg-label-main"))
                        yield _row("Worker label", Input(
                            value=self.config.label_worker, placeholder="e.g. Chakla",
                            id="cfg-label-worker"))

                    with Vertical(id="section-appearance", classes="config-section-panel hidden"):
                        yield Label("Appearance", classes="config-section")
                        yield _row("Theme", Select([(n, n) for n in THEMES], value=theme,
                                                   allow_blank=False, id="cfg-theme"))
                        yield _row("Lore", Switch(value=self.config.lore, id="cfg-lore"))

                    with Vertical(id="section-display", classes="config-section-panel hidden"):
                        yield Label("Display", classes="config-section")
                        yield _row("Show thinking", Switch(
                            value=self.config.show_thinking, id="cfg-show-thinking"))
                        yield _row("Show tool output", Switch(
                            value=self.config.show_tool_output, id="cfg-show-tool-output"))
                        _effort = (self.config.reasoning_effort
                                   if self.config.reasoning_effort in REASONING_EFFORTS else "medium")
                        yield _row("Reasoning effort", Select(
                            [(e, e) for e in REASONING_EFFORTS],
                            value=_effort, allow_blank=False, id="cfg-reasoning-effort"))
            with Horizontal(id="config-buttons"):
                yield Button("Cancel", id="cancel", variant="error")
                yield Button("Save", id="save", variant="success")

    @property
    def _riftor_app(self) -> "RiftorApp":
        return self.app  # type: ignore[return-value]

    def on_mount(self) -> None:
        # Model is the default-visible section (the only panel composed without
        # the `hidden` class); focus its first field.
        self.query_one("#cfg-provider", Select).focus()
        # Codex has no key/base/model-list — reflect that if it's the saved provider.
        self._set_codex_mode(self._provider == "codex")

    def _codex_status_text(self) -> str:
        st = auth_status()
        mark = "✓" if st.logged_in else "⚠"
        return f"{mark} {st.detail}"

    def _set_codex_mode(self, on: bool) -> None:
        """Codex has no API key/base/model-list: hide those rows, show status."""
        for wid in ("cfg-key", "cfg-base", "cfg-fetch"):
            row = self.query_one(f"#{wid}").parent
            if row is not None:
                row.set_class(on, "hidden")
        status_row = self.query_one("#cfg-codex-status").parent
        if status_row is not None:
            status_row.set_class(not on, "hidden")
        if on:
            self.query_one("#cfg-codex-status", Label).update(self._codex_status_text())

    def show_section(self, key: str) -> None:
        """Show the named section panel, hide the rest. All panels stay mounted
        (the Save path reads every field via query_one), so this only toggles
        the `hidden` class — it never adds or removes widgets.

        Guarded: ListView.Highlighted can fire while the screen is still
        composing (before the panels are mounted). query() returns an empty
        result set rather than raising, so we no-op until the panels exist."""
        if not self.query(".config-section-panel"):
            return
        for skey, _ in SECTIONS:
            panel = self.query_one(f"#section-{skey}", Vertical)
            if skey == key:
                panel.remove_class("hidden")
            else:
                panel.add_class("hidden")

    def on_list_view_highlighted(self, event: "ListView.Highlighted") -> None:
        if event.list_view.id != "config-nav" or event.item is None:
            return
        # item id is "nav-<key>"; strip the prefix to get the section key.
        item_id = event.item.id or ""
        if item_id.startswith("nav-"):
            self.show_section(item_id[len("nav-"):])

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "cfg-theme":
            value = event.value
            if isinstance(value, str) and value in THEMES:
                self._riftor_app._apply_theme(value)
            return
        if event.select.id == "cfg-provider" and isinstance(event.value, str):
            self._provider = event.value
            self._set_codex_mode(event.value == "codex")
            meta = PROVIDERS[event.value]
            self.query_one("#cfg-base", Input).value = meta.default_base or ""
            if self._provider_initialized:
                self._set_model_options(_model_options(event.value))
            else:
                self._provider_initialized = True
        elif event.select.id == "cfg-chakla-provider" and isinstance(event.value, str):
            self._worker_provider = event.value
            # NOTE: do NOT touch #cfg-base/#cfg-key here — those belong to the MAIN
            # provider. Switching the worker provider must not mutate the main
            # provider's fields. The worker's base/key are resolved at save time
            # in _open_config from the worker provider's stored creds / default base.
            if self._worker_provider_initialized:
                self._set_chakla_model_options(_model_options(event.value))
            else:
                self._worker_provider_initialized = True

    def _set_model_options(self, options: list[tuple[str, str]]) -> None:
        sel = self.query_one("#cfg-model-select", Select)
        sel.set_options(options or [("(type a custom id below)", "")])

    def _set_chakla_model_options(self, options: list[tuple[str, str]]) -> None:
        sel = self.query_one("#cfg-chakla-model-select", Select)
        sel.set_options(options or [("(type a custom id below)", "")])

    @work(thread=True, exclusive=True, group="fetch")
    def _fetch_models_worker(self, provider: str, base: str, key: str | None) -> None:
        result = fetch_models(provider, base or None, key or None)
        self.app.call_from_thread(self._apply_fetch_result, result)

    def _apply_fetch_result(self, result: FetchResult) -> None:
        if not self.is_running:  # screen dismissed while the fetch was in flight
            return
        self._set_model_options([(m, m) for m in result.models])
        if result.error:
            self._fail(f"fetch failed ({result.error[:60]}) — showing suggestions")
        else:
            self.query_one("#config-title", Label).update(
                f"riftor · config — {result.source}: {len(result.models)} models")

    def _revert_theme(self) -> None:
        self._riftor_app._apply_theme(self._original_theme)

    def action_cancel(self) -> None:
        self._revert_theme()
        self.dismiss(None)

    def _fail(self, message: str) -> None:
        self.query_one("#config-title", Label).update(f"riftor · config — {message}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self._revert_theme()
            self.dismiss(None)
            return
        if event.button.id == "cfg-fetch":
            key = self.query_one("#cfg-key", Input).value.strip() or None
            if not key:
                saved = self.config.providers.get(self._provider)
                key = saved.api_key if saved else None
            base = self.query_one("#cfg-base", Input).value.strip()
            self.query_one("#config-title", Label).update("riftor · config — fetching…")
            self._fetch_models_worker(self._provider, base, key)
            return
        try:
            temperature = float(self.query_one("#cfg-temp", Input).value)
        except ValueError:
            self._fail("temperature must be a number")
            return
        try:
            max_tokens = int(self.query_one("#cfg-maxtok", Input).value)
        except ValueError:
            self._fail("max tokens must be an integer")
            return
        try:
            max_steps = int(self.query_one("#cfg-maxsteps", Input).value)
        except ValueError:
            self._fail("tool call steps must be an integer")
            return
        if max_steps < 1:
            self._fail("tool call steps must be at least 1")
            return

        provider = self._provider
        custom = self.query_one("#cfg-model", Input).value.strip()
        sel_val = self.query_one("#cfg-model-select", Select).value
        chosen = custom or (sel_val if isinstance(sel_val, str) and sel_val else "")
        model = apply_prefix(provider, chosen) if chosen else self.config.model

        # --- worker (Chakla) model ---
        w_provider = self._worker_provider
        w_custom = self.query_one("#cfg-chakla-custom", Input).value.strip()
        w_sel = self.query_one("#cfg-chakla-model-select", Select).value
        w_chosen = w_custom or (w_sel if isinstance(w_sel, str) and w_sel else "")
        # Blank => "" => reuse main model at dispatch time.
        chakla_model = apply_prefix(w_provider, w_chosen) if w_chosen else ""

        result: dict = {
            "model": model,
            "provider": provider,
            "api_base": self.query_one("#cfg-base", Input).value.strip() or None,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "max_steps": max_steps,
            "theme": self.query_one("#cfg-theme", Select).value,
            "lore": self.query_one("#cfg-lore", Switch).value,
            "show_thinking": self.query_one("#cfg-show-thinking", Switch).value,
            "show_tool_output": self.query_one("#cfg-show-tool-output", Switch).value,
            "reasoning_effort": self.query_one("#cfg-reasoning-effort", Select).value,
            "chakla_model": chakla_model,
            "chakla_provider": w_provider if w_chosen else None,
            "label_main": self.query_one("#cfg-label-main", Input).value.strip()
                or self.config.label_main,
            "label_worker": self.query_one("#cfg-label-worker", Input).value.strip()
                or self.config.label_worker,
        }
        key = self.query_one("#cfg-key", Input).value.strip()
        if key:
            result["api_key"] = key
        self.dismiss(result)
