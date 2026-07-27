"""First-run onboarding wizard."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from riftor.providers import (
    PROVIDERS,
    PROVIDER_DEFAULTS,
    FetchResult,
    apply_prefix,
    fetch_models,
    provider_key_for_model,
)

if TYPE_CHECKING:
    from riftor.config import Config


def _model_options(provider_key: str) -> list[tuple[str, str]]:
    return [(m, m) for m in PROVIDER_DEFAULTS.get(provider_key, [])]


class OnboardingScreen(ModalScreen[dict | None]):
    """Multi-step first-run setup: provider → model → scope."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: "Config") -> None:
        super().__init__()
        self.config = config
        self._step = 0
        self._provider = provider_key_for_model(config.model)
        self._full_models: list[tuple[str, str]] = _model_options(self._provider)

    def compose(self) -> ComposeResult:
        meta = PROVIDERS[self._provider]
        env_key = meta.env
        prefilled = ""
        if env_key and os.environ.get(env_key):
            prefilled = os.environ[env_key]
        elif self.config.api_key:
            prefilled = self.config.api_key

        model_opts = self._full_models
        model_val = model_opts[0][1] if model_opts else Select.NULL

        with Vertical(id="onboard-box"):
            yield Static("Welcome to riftor", id="onboard-title")
            yield Static("", id="onboard-subtitle")
            with Vertical(id="onboard-body"):
                with Vertical(id="onboard-step-0", classes="onboard-step"):
                    yield Label("Choose your LLM provider")
                    yield Select(
                        [(m.label, k) for k, m in PROVIDERS.items()],
                        value=self._provider,
                        allow_blank=False,
                        id="ob-provider",
                    )
                    yield Label("API key (or set env var)")
                    yield Input(
                        password=True,
                        placeholder=f"or export {env_key or 'PROVIDER_API_KEY'}",
                        value=prefilled,
                        id="ob-key",
                    )
                with Vertical(id="onboard-step-1", classes="onboard-step hidden"):
                    yield Label("Model")
                    yield Select(
                        model_opts,
                        value=model_val,
                        allow_blank=True,
                        id="ob-model",
                    )
                    yield Label("Custom model id (optional override)")
                    yield Input(placeholder="e.g. deepseek-chat", id="ob-model-custom")
                    with Horizontal(classes="onboard-fetch-row"):
                        yield Button("Fetch live models", id="ob-fetch", variant="default")
                        yield Static("", id="ob-fetch-status")
                with Vertical(id="onboard-step-2", classes="onboard-step hidden"):
                    yield Label("Scope targets (comma or newline separated, optional)")
                    yield Input(placeholder="example.com  10.0.0.0/24", id="ob-scope")
            with Horizontal(id="onboard-buttons"):
                yield Button("Back", id="ob-back", variant="default")
                yield Button("Next", id="ob-next", variant="default")
                yield Button("Skip setup", id="ob-skip", variant="default")

    def on_mount(self) -> None:
        self._show_step(0)
        self._refresh_model_options(self._provider)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "ob-provider" and isinstance(event.value, str):
            self._provider = event.value
            self._refresh_model_options(event.value)

    def _refresh_model_options(self, provider_key: str) -> None:
        self._full_models = _model_options(provider_key) or [("(type below)", "")]
        sel = self.query_one("#ob-model", Select)
        sel.set_options(self._full_models)
        if self._full_models:
            sel.value = self._full_models[0][1]
        self.query_one("#ob-fetch-status", Static).update("")

    def _show_step(self, step: int) -> None:
        self._step = step
        titles = [
            "Step 1 of 3 — Provider & credentials",
            "Step 2 of 3 — Model",
            "Step 3 of 3 — Scope (optional)",
        ]
        self.query_one("#onboard-subtitle", Static).update(titles[step])
        for i in range(3):
            panel = self.query_one(f"#onboard-step-{i}", Vertical)
            if i == step:
                panel.remove_class("hidden")
            else:
                panel.add_class("hidden")
        self.query_one("#ob-back", Button).disabled = step == 0
        self.query_one("#ob-next", Button).label = "Finish" if step == 2 else "Next"

    def _resolved_key(self) -> str | None:
        typed = self.query_one("#ob-key", Input).value.strip()
        if typed:
            return typed
        meta = PROVIDERS.get(self._provider)
        if meta and meta.env:
            return os.environ.get(meta.env)
        return None

    @work(thread=True, exclusive=True, group="onboard-fetch")
    def _fetch_models_worker(self, provider: str, key: str | None) -> None:
        meta = PROVIDERS.get(provider)
        base = meta.default_base if meta else None
        result = fetch_models(provider, base, key)
        self.app.call_from_thread(self._apply_fetch_result, result)

    def _apply_fetch_result(self, result: FetchResult) -> None:
        if not self.is_running:
            return
        status = self.query_one("#ob-fetch-status", Static)
        if result.models:
            self._full_models = [(m, m) for m in result.models]
            sel = self.query_one("#ob-model", Select)
            sel.set_options(self._full_models)
            sel.value = self._full_models[0][1]
        if result.error:
            status.update(f"fetch failed — using defaults ({result.error[:40]})")
        else:
            status.update(f"{result.source}: {len(result.models)} models")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "ob-back":
            if self._step > 0:
                self._show_step(self._step - 1)
            return
        if bid == "ob-skip":
            self.dismiss({"onboarded": True})
            return
        if bid == "ob-fetch":
            prov_sel = self.query_one("#ob-provider", Select).value
            provider = prov_sel if isinstance(prov_sel, str) else self._provider
            self.query_one("#ob-fetch-status", Static).update("fetching…")
            self._fetch_models_worker(provider, self._resolved_key())
            return
        if bid == "ob-next":
            if self._step < 2:
                if self._step == 0:
                    prov = self.query_one("#ob-provider", Select).value
                    if isinstance(prov, str):
                        self._provider = prov
                        self._refresh_model_options(prov)
                self._show_step(self._step + 1)
                return
            prov = self.query_one("#ob-provider", Select).value
            if not isinstance(prov, str):
                prov = self._provider
            custom = self.query_one("#ob-model-custom", Input).value.strip()
            model_sel = self.query_one("#ob-model", Select).value
            chosen = custom or (str(model_sel) if model_sel else "")
            model = apply_prefix(prov, chosen) if chosen else self.config.model
            key = self._resolved_key()
            scope = self.query_one("#ob-scope", Input).value.strip()
            self.dismiss({
                "onboarded": True,
                "provider": prov,
                "model": model,
                "api_key": key,
                "scope": scope,
            })
