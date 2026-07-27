"""First-run onboarding wizard."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from riftor.providers import PROVIDERS, PROVIDER_DEFAULTS, apply_prefix, provider_key_for_model

if TYPE_CHECKING:
    from riftor.config import Config


class OnboardingScreen(ModalScreen[dict | None]):
    """Multi-step first-run setup: provider → model → scope."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, config: "Config") -> None:
        super().__init__()
        self.config = config
        self._step = 0
        self._provider = provider_key_for_model(config.model)

    def compose(self) -> ComposeResult:
        with Vertical(id="onboard-box"):
            yield Static("Welcome to riftor", id="onboard-title")
            yield Static("", id="onboard-subtitle")
            with Vertical(id="onboard-step-0"):
                yield Label("Choose your LLM provider")
                yield Select(
                    [(m.label, k) for k, m in PROVIDERS.items()],
                    value=self._provider,
                    allow_blank=False,
                    id="ob-provider",
                )
                yield Label("API key (or set env var)")
                yield Input(password=True, placeholder="sk-…", id="ob-key")
            with Vertical(id="onboard-step-1", classes="hidden"):
                yield Label("Model")
                opts = [(m, m) for m in PROVIDER_DEFAULTS.get(self._provider, [])]
                yield Select(opts, value=opts[0][1] if opts else Select.NULL, id="ob-model")
            with Vertical(id="onboard-step-2", classes="hidden"):
                yield Label("Scope targets (one per line, optional)")
                yield Input(placeholder="example.com  10.0.0.0/24", id="ob-scope")
            with Horizontal(id="onboard-buttons"):
                yield Button("Back", id="ob-back", variant="default")
                yield Button("Next", id="ob-next", variant="primary")
                yield Button("Skip", id="ob-skip", variant="default")

    def on_mount(self) -> None:
        self._show_step(0)

    def _show_step(self, step: int) -> None:
        self._step = step
        titles = [
            "Step 1 of 3 — Provider & credentials",
            "Step 2 of 3 — Model",
            "Step 3 of 3 — Scope (optional)",
        ]
        self.query_one("#onboard-title", Static).update("Welcome to riftor")
        self.query_one("#onboard-subtitle", Static).update(titles[step])
        for i in range(3):
            panel = self.query_one(f"#onboard-step-{i}", Vertical)
            if i == step:
                panel.remove_class("hidden")
            else:
                panel.add_class("hidden")
        back = self.query_one("#ob-back", Button)
        back.disabled = step == 0
        nxt = self.query_one("#ob-next", Button)
        nxt.label = "Finish" if step == 2 else "Next"

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
        if bid == "ob-next":
            if self._step < 2:
                if self._step == 0:
                    prov = self.query_one("#ob-provider", Select).value
                    if isinstance(prov, str):
                        self._provider = prov
                    # Rebuild model options for step 1
                    opts = [(m, m) for m in PROVIDER_DEFAULTS.get(self._provider, [])]
                    sel = self.query_one("#ob-model", Select)
                    sel.set_options(opts or [("(custom)", "")])
                self._show_step(self._step + 1)
                return
            # Finish
            prov = self.query_one("#ob-provider", Select).value
            if not isinstance(prov, str):
                prov = self._provider
            model_sel = self.query_one("#ob-model", Select).value
            model = apply_prefix(prov, str(model_sel)) if model_sel else self.config.model
            key = self.query_one("#ob-key", Input).value.strip() or None
            scope = self.query_one("#ob-scope", Input).value.strip()
            self.dismiss({
                "onboarded": True,
                "provider": prov,
                "model": model,
                "api_key": key,
                "scope": scope,
            })
