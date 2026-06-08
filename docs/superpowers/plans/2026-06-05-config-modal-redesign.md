# Config + Modal-Family Visual Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the `/config` modal into a sidebar/two-pane layout and apply a shared visual language (title/footer bars, dividers, focus glow, filled primary buttons) to the permission `ConfirmScreen` and `CommandDropdown`, so the whole modal layer reads as one coherent app.

**Architecture:** All styling lives in the single global stylesheet `riftor/tui/themes/rift.tcss` (the app's `CSS_PATH`). The only structural Python change is `config_screen.py`'s `compose()`, which gains a left-nav + per-section-panel layout; **all field widgets stay mounted at all times** (sections are toggled via a `.hidden` class) because the Save path reads every field by `query_one`. `permissions.py` and `widgets.py` are restyled purely via CSS — no Python edits. No new theme variables: the redesign reuses `$violet`, `$cyan`, `$border`, `$faint`, `$panel`, `$surface`, `$muted`, `$foreground`, which exist in all 7 themes.

**Tech Stack:** Python 3.11+, Textual (TUI framework), Textual CSS (`.tcss`), `uv` for tooling, `pytest` (offline) + `dev/smoke.py` (headless real-app test).

**Spec:** `docs/superpowers/specs/2026-06-05-config-modal-redesign-design.md`

**Reference — the design tokens (existing theme variables, do not invent new ones):**
- `$violet` — primary accent: active nav item, filled primary button, focus glow
- `$cyan` — secondary accent: section headings
- `$border` — dividers, bar borders
- `$faint` — subtlest hairlines
- `$panel` — modal card / bar background
- `$surface` — slightly-off background for the nav column
- `$muted` — idle label / nav-item text
- `$foreground` — active text

---

## Pre-flight

- [ ] **Step 0: Confirm branch and clean tree**

Run: `git status --short && git branch --show-current`
Expected: working tree clean (only this plan + the spec already committed), branch `ui/config-modal-redesign`.

If not on `ui/config-modal-redesign`: `git checkout ui/config-modal-redesign` (the branch already exists from the spec commit).

- [ ] **Step 1: Establish the baseline — all gates green before any change**

Run: `make check`
Expected: lint, typecheck, unit tests, and smoke (`SMOKE OK` etc.) all pass. This is the regression baseline; every later task re-runs the relevant subset.

---

## Task 1: Lock the "all panes stay mounted" contract into the smoke test (TDD — write the guard first)

This is the one real correctness constraint. We write the test **before** touching the layout, so when we refactor `compose()` the smoke test immediately tells us if any field stopped being query-able. The new assertions are written against ids/containers that don't exist yet, so the test will fail first (red), then pass after Task 2 (green).

**Files:**
- Modify: `dev/smoke.py` (the `/config` block, currently lines 156-165)

- [ ] **Step 1: Replace the `/config` smoke block with one that switches sections and verifies every field is query-able**

In `dev/smoke.py`, find this block (currently lines 156-165):

```python
        # /config opens the modal; escape cancels it
        from riftor.tui.config_screen import ConfigScreen

        inp.value = "/config"
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, ConfigScreen), type(app.screen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConfigScreen)
```

Replace it with:

```python
        # /config opens the sidebar modal. Verify: (1) it mounts, (2) every
        # field id stays query-able regardless of which section is active —
        # this is the load-bearing contract, since Save reads them all via
        # query_one and the sidebar must NOT unmount off-screen sections,
        # (3) switching the active section toggles panel visibility,
        # (4) escape cancels.
        from riftor.tui.config_screen import ConfigScreen
        from textual.widgets import Input as _Input

        inp.value = "/config"
        await pilot.press("enter")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ConfigScreen), type(screen)

        # Every field across every section must be mounted up front. If the
        # sidebar refactor ever unmounts a hidden section, these raise.
        all_field_ids = [
            "#cfg-provider", "#cfg-model-select", "#cfg-model", "#cfg-base", "#cfg-key",
            "#cfg-temp", "#cfg-maxtok",
            "#cfg-chakla-provider", "#cfg-chakla-model-select", "#cfg-chakla-custom",
            "#cfg-label-main", "#cfg-label-worker",
            "#cfg-theme", "#cfg-lore",
            "#cfg-show-thinking", "#cfg-show-tool-output", "#cfg-reasoning-effort",
        ]
        for fid in all_field_ids:
            assert screen.query(fid), f"field {fid} not mounted in sidebar config"

        # The five section panels exist and exactly one is visible at a time.
        panel_ids = [
            "#section-model", "#section-generation", "#section-workers",
            "#section-appearance", "#section-display",
        ]
        for pid in panel_ids:
            assert screen.query(pid), f"section panel {pid} missing"
        visible = [pid for pid in panel_ids
                   if not screen.query_one(pid).has_class("hidden")]
        assert visible == ["#section-model"], f"expected only Model visible, got {visible}"

        # Switch to the Workers section via the screen's section selector and
        # confirm visibility moved there while fields stay query-able.
        screen.show_section("workers")
        await pilot.pause()
        visible = [pid for pid in panel_ids
                   if not screen.query_one(pid).has_class("hidden")]
        assert visible == ["#section-workers"], f"expected Workers visible, got {visible}"
        # a Model-section field is still mounted even though Model is hidden
        assert screen.query("#cfg-provider"), "hidden-section field must stay mounted"

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ConfigScreen)
```

- [ ] **Step 2: Run the smoke test to verify it FAILS (red)**

Run: `uv run python dev/smoke.py`
Expected: FAIL inside the `/config` block — either `AttributeError: 'ConfigScreen' object has no attribute 'show_section'` or an assertion error about `#section-model` missing. This proves the guard is exercising the new design before it exists.

- [ ] **Step 3: Commit the failing guard**

```bash
git add dev/smoke.py
git commit -m "test: guard config sidebar contract (all fields mounted, section toggle)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Refactor `config_screen.py` compose() into the sidebar layout (make the guard pass)

Convert the flat scrolling body into: title bar → `Horizontal#config-main` containing a left nav + a content area of five always-mounted per-section panels → footer bar. Add a `show_section(key)` method and a nav-change handler. **Preserve every field id and the dismiss-dict shape** so `app.py` and the Save path are untouched.

**Files:**
- Modify: `riftor/tui/config_screen.py`
  - the module-level helpers (lines 30-36)
  - `compose()` (lines 56-148)
  - add a section-nav mechanism + `show_section` + `on_mount` (around lines 154-155)

- [ ] **Step 1: Add a section-panel helper and a nav-items constant near the existing `_row` helper**

In `config_screen.py`, the existing helper at lines 30-32 is:

```python
def _row(label: str, field: Widget) -> Horizontal:
    """A label-column + field row, so every field's left edge lines up."""
    return Horizontal(Label(label, classes="field-label"), field, classes="field-row")
```

Immediately after it, add:

```python
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
```

- [ ] **Step 2: Update the imports at the top of `config_screen.py`**

The current widget import (line 12) is:

```python
from textual.widgets import Button, Input, Label, Rule, Select, Switch
```

`Rule` is no longer used after this refactor (the per-section `Rule()` dividers are removed). Replace the line with:

```python
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Switch
```

(We use a `ListView` for the keyboard-navigable left nav. `Rule` is dropped.)

- [ ] **Step 3: Rewrite `compose()` into the sidebar layout**

Replace the entire `compose()` method body (the `with Vertical(id="config-box"):` block, currently lines 87-148) with the structure below. **All the per-field value-computation code above it (lines 56-86) stays exactly as-is** — only the widget tree changes.

```python
        with Vertical(id="config-box"):
            yield Label("riftor · config", id="config-title")
            with Horizontal(id="config-main"):
                # left nav — keyboard-focusable; selection drives show_section()
                lv = ListView(id="config-nav")
                lv.extend(
                    ListItem(Label(label), id=f"nav-{key}") for key, label in SECTIONS
                )
                yield lv
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
                        with Horizontal(classes="field-row"):
                            yield Label("", classes="field-label")
                            yield Button("Fetch models", id="cfg-fetch", variant="primary")

                    with Vertical(id="section-generation", classes="config-section-panel hidden"):
                        yield Label("Generation", classes="config-section")
                        yield _row("Temperature", Input(value=str(self.config.temperature), id="cfg-temp"))
                        yield _row("Max tokens", Input(value=str(self.config.max_tokens), id="cfg-maxtok"))

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
```

Notes baked into the above:
- Section headings changed from ALL-CAPS to title-case (`"Model"` not `"MODEL"`); the small-caps look now comes from CSS `text-transform`, not literal text. This is cosmetic and touches no logic.
- The footer order is now `Cancel` then `Save` so the filled primary `Save` sits rightmost (CSS right-aligns the bar). Button ids (`save`/`cancel`) and variants are unchanged, so `on_button_pressed` is unaffected.
- Every `id="cfg-..."` is identical to the original — the Save path's `query_one` calls keep working.

- [ ] **Step 4: Add `show_section`, the nav-change handler, and update `on_mount`**

The current `on_mount` (lines 154-155) is:

```python
    def on_mount(self) -> None:
        self.query_one("#cfg-provider", Select).focus()
```

Replace it with the following three methods (keep `show_section` and `on_list_view_selected`/`on_list_view_highlighted` together):

```python
    def on_mount(self) -> None:
        # Model is the default-visible section; focus its first field.
        self.query_one("#config-nav", ListView).index = 0
        self.query_one("#cfg-provider", Select).focus()

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
```

- [ ] **Step 5: Run the smoke test to verify it PASSES (green)**

Run: `uv run python dev/smoke.py`
Expected: `SMOKE OK` (and all the other `... OK` lines). The Task 1 guard now passes: all fields are mounted, `show_section("workers")` flips visibility, and escape still cancels.

- [ ] **Step 6: Run lint + typecheck (the import change and new methods must be clean)**

Run: `uv run ruff check riftor dev tests && uv run pyright riftor`
Expected: no errors. (If pyright flags the `ListView.extend` generator, wrap it in `list(...)`; if it flags `event.item` typing, the `is None` guard already narrows it.)

- [ ] **Step 7: Commit the layout refactor**

```bash
git add riftor/tui/config_screen.py
git commit -m "feat(tui): config modal sidebar layout (nav + per-section panels)

All field widgets stay mounted (sections toggle a .hidden class) so the
query_one-based Save path is unchanged. Field ids and dismiss-dict shape
preserved; app.py untouched.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Style the config sidebar in `rift.tcss` (split shared selectors first)

Now the structure exists; give it the visual language. First **split** the selectors currently shared between the confirm and config modals so config-only changes don't bleed into the permission prompt, then add the sidebar/bar/divider/focus-glow rules.

**Files:**
- Modify: `riftor/tui/themes/rift.tcss` (the modal block, lines 73-200)

- [ ] **Step 1: Split the shared modal selectors into separate config / confirm rules**

In `rift.tcss`, find this shared block (lines 73-105):

```css
/* ───────────────────────── modals: permission + config ───────────────────── */
ConfirmScreen, ConfigScreen {
    align: center middle;
    background: $background 75%;
}

#confirm-box, #config-box {
    width: 72;
    max-width: 90%;
    height: auto;
    max-height: 90%;
    padding: 1 2 1 2;
    background: $panel;
    border: round $violet;
}

/* config card lays out as: title (auto) · body (flexes/scrolls) · footer (auto).
   The flexing body is what keeps the Save/Cancel footer on-screen even on short
   terminals — without this the body's fixed height pushed the buttons off the
   bottom. */
#config-box {
    layout: vertical;
}

/* title bar — bold, with an underline rule that spans the card */
#confirm-title, #config-title {
    width: 1fr;
    padding: 0 0 1 0;
    margin-bottom: 1;
    border-bottom: solid $border;
    color: $violet;
    text-style: bold;
}
```

Replace that whole block with this (keeps the confirm modal exactly as it looks today via its own rules; gives config the new card-with-bars treatment):

```css
/* ───────────────────────── modals: permission + config ───────────────────── */
ConfirmScreen, ConfigScreen {
    align: center middle;
    background: $background 75%;
}

/* --- confirm modal: unchanged classic card (its own rules now) --- */
#confirm-box {
    width: 72;
    max-width: 90%;
    height: auto;
    max-height: 90%;
    padding: 1 2 1 2;
    background: $panel;
    border: round $violet;
}
#confirm-title {
    width: 1fr;
    padding: 0 0 1 0;
    margin-bottom: 1;
    border-bottom: solid $border;
    color: $violet;
    text-style: bold;
}

/* --- config modal: bars + sidebar; no card padding so bars span edge-to-edge --- */
#config-box {
    layout: vertical;
    width: 80;
    max-width: 92%;
    height: auto;
    max-height: 90%;
    padding: 0;
    background: $panel;
    border: round $violet;
}
/* title BAR — own background, spans the card, divider underneath */
#config-title {
    width: 1fr;
    height: 3;
    padding: 1 2 0 2;
    background: $surface;
    border-bottom: solid $border;
    color: $foreground;
    text-style: bold;
}
```

- [ ] **Step 2: Add the sidebar (nav + content pane) rules**

Find the config body/field block (lines 107-148):

```css
/* — config body & field rows — */
/* 1fr so the body takes only the space left after the title + footer, and
   scrolls inside it. This guarantees the footer buttons stay visible on short
   terminals. height:auto would let the body grow and shove the footer off. */
#config-body {
    height: 1fr;
    padding: 0;
}

.config-section {
    width: 1fr;
    color: $cyan;
    text-style: bold;
    margin: 1 0 0 0;
}

.field-row {
    height: 3;
    align: left middle;
    margin-bottom: 0;
}

.field-label {
    width: 14;
    height: 3;
    color: $muted;
    content-align: left middle;
}

.field-row Input,
.field-row Select {
    width: 1fr;
}

.field-row Switch {
    height: 3;
}

#config-body Rule {
    color: $border;
    margin: 0;
}
```

Replace that whole block with:

```css
/* — config sidebar: nav column + content pane — */
/* The middle band flexes (1fr) so the title and footer bars stay pinned. */
#config-main {
    height: 1fr;
    layout: horizontal;
}

/* left nav — slightly-off background + a divider bar on its right edge */
#config-nav {
    width: 20;
    height: 1fr;
    background: $surface;
    border-right: solid $border;
    padding: 1 0;
    scrollbar-color: $border;
}
#config-nav > ListItem {
    padding: 0 2;
    color: $muted;
}
#config-nav > ListItem.--highlight {
    background: $violet 25%;
    color: $foreground;
    border-left: thick $violet;
}

/* right content pane — scrolls if a section is tall */
#config-pane {
    width: 1fr;
    height: 1fr;
    padding: 1 2;
    scrollbar-color: $border;
}

/* only the active section panel is shown */
.config-section-panel {
    height: auto;
}
.config-section-panel.hidden {
    display: none;
}

.config-section {
    width: 1fr;
    color: $cyan;
    text-style: bold;
    margin: 0 0 1 0;
}
/* NOTE: Textual does NOT support `text-transform`. Section headings render
   title-case ("Model") as the compose() text passes them — which matches the
   approved sidebar mockup. Do not add `text-transform: uppercase` here; it
   crashes CSS parse. If uppercase is ever wanted, uppercase the literal text
   in config_screen.py instead. */

.field-row {
    height: 3;
    align: left middle;
    margin-bottom: 0;
}

.field-label {
    width: 14;
    height: 3;
    color: $muted;
    content-align: left middle;
}

.field-row Input,
.field-row Select {
    width: 1fr;
}

.field-row Switch {
    height: 3;
}

/* focus glow on the active field — extends the #prompt:focus convention */
.field-row Input:focus,
.field-row Select:focus {
    border: round $violet;
}
```

- [ ] **Step 3: Split + restyle the footer bar (config gets a real bar; confirm keeps its row)**

Find the shared footer block (lines 172-182):

```css
/* — shared footer — */
#confirm-buttons, #config-buttons {
    height: auto;
    align: center middle;
    padding-top: 1;
}

#confirm-buttons Button, #config-buttons Button {
    min-width: 12;
    margin: 0 1;
}
```

Replace it with:

```css
/* — confirm footer: unchanged centered row — */
#confirm-buttons {
    height: auto;
    align: center middle;
    padding-top: 1;
}
#confirm-buttons Button {
    min-width: 12;
    margin: 0 1;
}

/* — config footer BAR: own background, top divider, right-aligned actions — */
#config-buttons {
    height: 3;
    align: right middle;
    background: $surface;
    border-top: solid $border;
    padding: 0 2;
}
#config-buttons Button {
    min-width: 12;
    margin: 0 0 0 1;
}
/* filled primary Save; outline Cancel */
#config-buttons #save {
    background: $violet;
    color: $background;
    text-style: bold;
}
#config-buttons #cancel {
    background: $panel;
    color: $muted;
    border: round $border;
}
```

- [ ] **Step 4: Run the smoke test (CSS must parse and the modal must still mount/cancel)**

Run: `uv run python dev/smoke.py`
Expected: `SMOKE OK`. Textual parses `.tcss` at app start; a CSS syntax error would crash `app.run_test()` here. The Task-1 assertions still pass (CSS doesn't change ids).

- [ ] **Step 5: Manually verify the sidebar look across a dark and a light theme**

Run: `uv run riftor`
Then: type `/config`, arrow ↑/↓ through the nav (sections should swap with no scroll, active item shows the violet bar), tab into a field (violet focus glow), look at the title bar / footer bar / filled Save. Press `escape`. Then `/theme dawn`, reopen `/config`, confirm the bars/nav/Save read correctly on the light theme (no invisible text, the filled Save has readable contrast). Repeat sanity check with `/theme rift`. Quit with `ctrl+c` or `/quit`.

Expected: coherent sidebar in both themes; no unreadable contrast. If the light theme's filled `Save` (`color: $background`) is low-contrast, note it — `$background` on light themes is near-white, which is correct against the `$violet` fill, but verify by eye.

- [ ] **Step 6: Commit the config styling**

```bash
git add riftor/tui/themes/rift.tcss
git commit -m "style(tui): sidebar config look — bars, nav, dividers, focus glow

Split the shared #confirm-*/#config-* selectors so config-only styling
no longer bleeds into the permission modal. Reuses existing theme vars;
no new variables, so all 7 themes adapt.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Apply the shared visual language to the permission `ConfirmScreen` (CSS-only)

Give the permission prompt the matching title-bar + footer-bar + filled-primary look, purely in `rift.tcss`. No Python edits (the title/scope/detail colors stay as their current hardcoded Rich hex — a deliberate scope decision).

**Files:**
- Modify: `riftor/tui/themes/rift.tcss` (the `#confirm-*` rules added/kept in Task 3, plus the diff panel block lines 150-170)

- [ ] **Step 1: Upgrade `#confirm-title` into a title bar and `#confirm-buttons` into a footer bar**

In `rift.tcss`, the `#confirm-title` rule (from Task 3 Step 1) currently is:

```css
#confirm-title {
    width: 1fr;
    padding: 0 0 1 0;
    margin-bottom: 1;
    border-bottom: solid $border;
    color: $violet;
    text-style: bold;
}
```

Replace it with a bar that matches config (note: the *text* color still comes from the inline Rich style in `permissions.py`; this rule controls the bar background + divider + bold weight):

```css
#confirm-title {
    width: 1fr;
    height: auto;
    padding: 1 2;
    margin-bottom: 1;
    background: $surface;
    border-bottom: solid $border;
    text-style: bold;
}
```

And the `#confirm-buttons` rule (from Task 3 Step 3):

```css
#confirm-buttons {
    height: auto;
    align: center middle;
    padding-top: 1;
}
```

Replace with a footer bar that keeps all five buttons centered but adds the bar chrome and a filled primary (the auto-focused "Once" / `#once`):

```css
#confirm-buttons {
    height: 3;
    align: center middle;
    background: $surface;
    border-top: solid $border;
    padding: 0 1;
}
#confirm-buttons Button {
    min-width: 12;
    margin: 0 1;
}
/* filled primary = Allow once (the auto-focused, safest affirmative) */
#confirm-buttons #once {
    background: $violet;
    color: $background;
    text-style: bold;
}
```

Because `#confirm-box` keeps `padding: 1 2` (from Task 3), give the title/footer bars negative-free edge-to-edge feel by leaving the box padding as-is — the bars sit inside the padded card, which is acceptable for the confirm modal (it's smaller and not a sidebar). Do **not** change `#confirm-box`.

- [ ] **Step 2: Add a focus-glow rule for the confirm buttons**

Directly after the `#confirm-buttons #once` rule, add:

```css
#confirm-buttons Button:focus {
    text-style: bold reverse;
}
```

(Textual's `reverse` gives a clear focus indication on a button without needing a per-theme color; this matches "focus glow" within the terminal's means for buttons.)

- [ ] **Step 3: Run the smoke test (the scope-deny path exercises ConfirmScreen indirectly)**

Run: `uv run python dev/smoke.py`
Expected: `SMOKE OK`. The smoke test's scope block (lines 73-94) stubs `push_screen_wait`, so it doesn't render `ConfirmScreen`, but CSS parse errors would still crash app start. This confirms the CSS is valid.

- [ ] **Step 4: Manually verify the permission prompt look**

Run: `uv run riftor`
Trigger a permission prompt: ensure no allow rule exists, then ask the agent to run something dangerous, OR more simply set a tiny scope and run a bash command out of scope. (If you have no model configured, skip to the visual check by temporarily adding a manual trigger — otherwise rely on the next reviewer's manual pass.) Confirm: title bar with its background + divider, the five buttons sitting in a footer bar, "Once" filled violet, focused button shows reverse. Try a light theme too.

Expected: the permission prompt now visually matches the config modal's bar/footer language. If a model isn't configured for a live trigger, note that this step was verified by inspection of the CSS + the config modal parity (same rules), and defer the live visual to the reviewer.

- [ ] **Step 5: Commit the confirm-modal styling**

```bash
git add riftor/tui/themes/rift.tcss
git commit -m "style(tui): permission modal matches config — title/footer bars, filled primary

CSS-only; permissions.py untouched (title colors stay as existing inline
Rich hex, per the design decision).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Polish the `CommandDropdown` to match (CSS-only)

Align the slash-command popover's border, padding, highlight, and scrollbar with the modal language. Pure CSS — `CommandDropdown` applies no styling in Python.

**Files:**
- Modify: `riftor/tui/themes/rift.tcss` (the command-dropdown block, lines 202-231)

- [ ] **Step 1: Restyle the dropdown block**

In `rift.tcss`, find the command-dropdown block (lines 202-231):

```css
/* ──────────────────────────── command dropdown ──────────────────────── */
#cmd-dropdown {
    display: none;
    height: auto;
    max-height: 12;
    margin: 0 1 0 1;
    border: round $violet;
    background: $panel;
    padding: 0;
}

#cmd-dropdown.visible {
    display: block;
}

#cmd-dropdown ListView {
    background: $panel;
    color: $foreground;
    padding: 0 1;
}

#cmd-dropdown ListView > ListItem {
    padding: 0 1;
    color: $muted;
}

#cmd-dropdown ListView > ListItem.--highlight {
    background: $violet 30%;
    color: $foreground;
}
```

Replace it with (adds breathing room, a scrollbar color, and aligns the highlight with the config nav's active-item treatment — violet tint + accent left-bar):

```css
/* ──────────────────────────── command dropdown ──────────────────────── */
#cmd-dropdown {
    display: none;
    height: auto;
    max-height: 12;
    margin: 0 1 0 1;
    border: round $violet;
    background: $panel;
    padding: 0;
    scrollbar-color: $border;
}

#cmd-dropdown.visible {
    display: block;
}

#cmd-dropdown ListView {
    background: $panel;
    color: $foreground;
    padding: 1 1;
}

#cmd-dropdown ListView > ListItem {
    padding: 0 2;
    color: $muted;
}

/* match the config nav active-item: violet tint + accent left-bar */
#cmd-dropdown ListView > ListItem.--highlight {
    background: $violet 25%;
    color: $foreground;
    border-left: thick $violet;
}
```

- [ ] **Step 2: Run the smoke test**

Run: `uv run python dev/smoke.py`
Expected: `SMOKE OK`. (The dropdown isn't directly asserted, but CSS must parse.)

- [ ] **Step 3: Manually verify the dropdown**

Run: `uv run riftor`
Type `/` in the prompt. Confirm: the dropdown shows with the rounded violet border, items have padding/breathing room, the highlighted item has the violet tint + left accent bar (matching the config nav). Arrow up/down to move the highlight. Press `escape` to dismiss. Check on a light theme too.

Expected: dropdown visually consistent with the config nav active-state.

- [ ] **Step 4: Commit the dropdown polish**

```bash
git add riftor/tui/themes/rift.tcss
git commit -m "style(tui): command dropdown matches modal language (padding, highlight, scrollbar)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Full verification + final review

**Files:** none (verification only)

- [ ] **Step 1: Run all CI gates**

Run: `make check`
Expected: lint ✓, typecheck ✓, unit tests ✓, smoke `SMOKE OK` ✓ — all green.

- [ ] **Step 2: Full manual sweep across themes**

Run: `uv run riftor`
Walk the full modal layer in one session:
1. `/config` → arrow through all 5 sections, tab through fields (focus glow), check title/footer bars + filled Save, `escape`.
2. `/` → check the dropdown highlight, `escape`.
3. (If a model is configured) trigger a permission prompt and check its bars/filled-primary.
4. `/theme dawn` (light) → repeat 1 & 2; confirm contrast.
5. `/theme rift` → confirm back to default.
6. `/quit`.

Expected: the config modal, command dropdown, and permission prompt all read as one coherent visual language, in both a dark and a light theme.

- [ ] **Step 3: Verify the diff is scoped to the four intended files**

Run: `git diff --stat main...HEAD`
Expected: exactly these files changed — `dev/smoke.py`, `riftor/tui/config_screen.py`, `riftor/tui/themes/rift.tcss`, plus the two docs (spec + plan). **No** changes to `riftor/safety/permissions.py`, `riftor/tui/widgets.py`, `riftor/tui/app.py`, or `riftor/tui/theme.py` — confirming the CSS-only restyle stayed CSS-only and no new theme vars were added.

- [ ] **Step 4: (Optional) Update the visual companion / clean up the brainstorm server**

Run: `/home/amanverasia/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/brainstorming/scripts/stop-server.sh /home/amanverasia/Projects/riftor/.superpowers/brainstorm/638330-1780656381`
Expected: server stopped. (Mockups persist under `.superpowers/` which is gitignored.)

---

## Self-Review Notes (for the implementer)

- **Spec coverage:** §3 sidebar → Tasks 1-3; §3.3 all-mounted contract → Task 1 guard + Task 2 `show_section`; §4 ConfirmScreen → Task 4; §5 CommandDropdown → Task 5; §6 CSS split → Task 3 Step 1 + Task 4; §8 testing → smoke extension (Task 1) + Task 6. §4's "keep hardcoded hexes" decision → honored (no `permissions.py` edit). §9 out-of-scope items are not touched.
- **No new theme vars** (§2 / spec §10 risk): verified in Task 6 Step 3 by the `git diff --stat` excluding `theme.py`.
- **`app.py` untouched** (dismiss-dict + save path contract): field ids and button ids are byte-for-byte preserved in Task 2 Step 3; verified in Task 6 Step 3.
- **If `ListView` proves awkward for nav** (e.g. focus stealing from fields): the design explicitly defers the nav-widget choice. A fallback is a column of `Button`s with `on_button_pressed` calling `show_section`; the `show_section` method and all CSS class names stay identical, so only Task 2 Steps 1-4 change, not Task 3.
