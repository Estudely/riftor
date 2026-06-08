# Config + Modal-Family Visual Redesign — Design

**Date:** 2026-06-05
**Status:** Design (awaiting review)
**Scope:** Track 1 of the riftor UI work — "make the terminal app look nicer," starting with the `/config` modal and extending a shared visual language to the permission prompt and command dropdown.

> This is the first of two planned UI tracks. Track 2 (extracting a UI-agnostic agent-loop core for a future webapp) is noted separately in memory and is **out of scope** here.

---

## 1. Goal & Motivation

The current Textual UI is functional but reads as flat and dated — uniform rounded-violet borders, tight uniform spacing, bare bold section labels, and default Textual buttons. The `/config` modal is the worst offender and the screen the user named directly.

The fix is **design work inside Textual**, not a framework change. Everything below renders in the terminal (real Unicode borders, cell grid) — the improvement comes from layout, spacing, border treatment, type hierarchy, and accent discipline. This was validated against faithful in-palette mockups; the user selected the **sidebar / two-pane** direction for the config modal and chose to extend the resulting visual language to the whole modal family for consistency.

**Success criteria:**
- The `/config` modal becomes a sidebar layout: left nav of sections, right pane showing one section at a time, no scrolling within a section.
- The permission `ConfirmScreen` and the `CommandDropdown` adopt the same visual language (title/footer bars, dividers, focus glow, filled primary buttons) so the modal layer reads as one coherent app.
- All 7 themes continue to work with **no new theme variables**.
- No regression: saving config, live theme preview, model fetch, and permission decisions all behave exactly as before.

---

## 2. The Visual Language (shared vocabulary)

A small set of consistent treatments applied across all three surfaces:

| Element | Treatment |
|---|---|
| **Title bar** | Full-width bar with its own background (`$panel`), bold title text, `border-bottom: solid $border`. |
| **Footer bar** | Full-width bar with its own background (`$panel`), `border-top: solid $border`, right-aligned actions. |
| **Primary button** | Filled (`background: $violet`, dark text) — the affirmative action (Save / Allow-once). |
| **Secondary button** | Outline only (`border` + `$muted` text) — Cancel / Deny. |
| **Section divider** | A hairline rule (`$border`/`$faint`) with a small caps label, instead of bare bold text. |
| **Focus glow** | Focused field/nav item gets a `$violet` border + subtle tint, extending the existing `#prompt:focus` pattern. |
| **Active nav item** | `background: $violet` (alpha) + `border-left: thick $violet` + `$foreground` text — reuses the existing `.--highlight` accent convention. |

**Palette:** all of the above use existing variables (`$violet`, `$cyan`, `$border`, `$faint`, `$panel`, `$surface`, `$muted`, `$foreground`). No additions to `theme.py` are required, so every theme adapts automatically.

---

## 3. Component: Config Modal (sidebar)

### 3.1 Layout

```
┌──────────────────────────────────────────────────────┐
│ riftor · config            ↑↓ section · tab · esc      │  ← title bar
├──────────────┬─────────────────────────────────────────┤
│ ◆ Model      │  Provider   [ Anthropic            ▾ ]  │
│   Generation │  Model      [ claude-opus-4-8      ▾ ]  │  ← right pane:
│ 🐦 Workers   │  Custom id  [ override (optional)    ]  │    active section
│   Appearance │  Base URL   [ provider default       ]  │    only
│   Display    │  API key    [ leave blank to keep    ]  │
│              │  [ ⟲ Fetch models ]                     │
├──────────────┴─────────────────────────────────────────┤
│                                     [ Cancel ] [ Save ] │  ← footer bar
└──────────────────────────────────────────────────────┘
```

- **Left nav** (fixed width ~16–20 cols): the five existing sections, in display order — **Model · Generation · Workers · Appearance · Display**. Optional leading glyphs (kept, per user). A vertical divider (`border-right: solid $border`) separates nav from content.
- **Right pane**: shows only the active section's fields. Field rows keep the existing label-column alignment (`.field-label` width), with a touch more vertical breathing room.
- **Title bar** shows the current status-message text (preserves the runtime `#config-title` `.update()` behavior used by model-fetch).
- **Footer bar**: right-aligned `Cancel` (outline) + `Save` (filled primary).

### 3.2 Section → field mapping (unchanged content)

| Nav item | Fields (existing ids preserved) |
|---|---|
| Model | `#cfg-provider`, `#cfg-model-select`, `#cfg-model`, `#cfg-base`, `#cfg-key`, `#cfg-fetch` (button) |
| Generation | `#cfg-temp`, `#cfg-maxtok` |
| Workers | `#cfg-chakla-provider`, `#cfg-chakla-model-select`, `#cfg-chakla-custom`, `#cfg-label-main`, `#cfg-label-worker` |
| Appearance | `#cfg-theme`, `#cfg-lore` |
| Display | `#cfg-show-thinking`, `#cfg-show-tool-output`, `#cfg-reasoning-effort` |

### 3.3 Critical constraint: all panes stay mounted

The Save path (`on_button_pressed`) and the Select/fetch handlers read **every field by `query_one(#id)`**. Therefore the redesign **must keep all field widgets mounted at all times** and switch sections by toggling a `.hidden` class (`display: none`) on per-section panels — **never** by conditionally composing or removing off-screen sections, which would make `query_one` raise at save time.

- Each section becomes a container (e.g. `Vertical.config-section-panel` with ids `#section-model` … `#section-display`).
- A new nav-change handler flips which panel has `.hidden`, mirroring the existing `on_select_changed` / `on_button_pressed` routing.
- The dismiss-dict shape returned to `app.py._open_config` is **unchanged** — `app.py` needs no edits.

### 3.4 Behaviors preserved

- **Live theme preview**: `#cfg-theme` change → `_apply_theme(value)`; `escape`/Cancel → `_revert_theme()`. Unchanged.
- **Model fetch**: `#cfg-fetch` → background worker → `_apply_fetch_result` updates `#config-title`. The `#config-title` id is **kept** so the three mutation call sites (fetch handler, `_apply_fetch_result`, `_fail`) need no changes.
- **Initial focus**: `on_mount` focuses `#cfg-provider`, which lives in the default-visible **Model** pane — valid as long as Model is the section shown first. (Alternative: move initial focus to the nav.)
- **Keyboard nav**: the nav is keyboard-focusable; `↑/↓` moves between nav sections (which flips the visible pane), `tab` cycles fields within the active pane, `esc` cancels. The on-screen hint line documents this. The concrete nav widget (e.g. a focusable `ListView`/`OptionList`, or a column of `Button`s) is an **implementation choice deferred to the plan** — the design requires only that switching sections toggles `.hidden` on the panels (§3.3) and that the active item shows the active-nav treatment (§2).

---

## 4. Component: Permission Modal (`ConfirmScreen`)

Same visual language, applied to the existing structure (`#confirm-box` → `#confirm-title` / `#confirm-scope` / `#confirm-detail` / `#confirm-diff` / `#confirm-buttons`).

- **Title bar**: `#confirm-title` gains its own `$panel` background + `border-bottom`, matching config. Two states kept: "permission required · {tool}" and the scope-warning "⚠ OUT OF SCOPE · {tool}".
- **Footer bar**: `#confirm-buttons` becomes a footer bar (`border-top`). The five decisions keep their `variant=` and key bindings (`a/s/w/d/esc`); **Allow once** is the filled primary (auto-focused), the rest follow the secondary/destructive treatment.
- **Diff panel** (`#confirm-diff`): keep its bordered `$tool-bg` look, aligned with the new divider language.
- **Focus glow**: add a `:focus` rule for the confirm buttons (none exists today).

**No new imports.** `permissions.py` already imports the Textual/Rich surface it needs. All of the above is achievable purely in `rift.tcss`.

**One optional Python edit:** the title/scope/detail/diff colors are hardcoded Rich hex inside `compose()`/`_render_detail()` (e.g. `#f0abfc`, `#fca5a5`, `#e9e9f2`), and inline Rich style wins over tcss on the glyph text. To make the title bar fully honor the theme palette, those literal strings would be edited in place (still **no new import** — `Text` is already imported). **Decision: accept the existing hardcoded hexes for now** so the confirm restyle stays 100% tcss-only and the safety file is untouched. (We can theme-drive them in a later pass if desired.)

---

## 5. Component: Command Dropdown (`CommandDropdown`)

The slash-command popover (`#cmd-dropdown` → `ListView` → `ListItem`). It applies **no styling in Python** — every color/border comes from `rift.tcss`, so this is a pure-CSS change.

- **Border/chrome**: align with the modal language (it already shares the `round $violet` token; refine to match the polished cards).
- **Padding**: give the list and items breathing room (currently flush `padding: 0`).
- **Highlight** (`.--highlight`): align the selected-row treatment with the config nav's active-state (filled `$violet` alpha + accent), instead of the current flat 30% tint. Keep the exact `.--highlight` selector (it's Textual's built-in class).
- **Scrollbar**: add `scrollbar-color: $border` to match the modal scroll treatment.

No compose() change (a header/divider element is possible but explicitly **out of scope**).

---

## 6. CSS Organization (`rift.tcss`)

Four selector groups are currently **shared** between the two modals:
`#confirm-box, #config-box` · `#confirm-title, #config-title` · `#confirm-buttons, #config-buttons` · `#confirm-buttons Button, #config-buttons Button`.

**Split these** so the config sidebar layout (nav column, content pane, dividers) targets `#config-*` only and does not bleed into the confirm modal. The shared *visual vocabulary* (bar backgrounds, filled buttons, focus glow) can stay shared where the two modals genuinely match, but the **structural** sidebar rules are config-only.

New selectors to add (config): `#config-main` (the nav+pane Horizontal), `.config-nav` / `.config-nav-item` / `.config-nav-item.-active`, `.config-section-panel` + `.hidden`, content-pane `1fr` rules, `.field-row Input:focus` / `.field-row Select:focus` glow, footer-bar border.

---

## 7. Files Touched

| File | Change type | Summary |
|---|---|---|
| `riftor/tui/themes/rift.tcss` | **Pure CSS (bulk)** | Split shared selectors; add sidebar/nav/pane rules, title+footer bars, filled buttons, focus glow, dropdown polish. |
| `riftor/tui/config_screen.py` | **Structural compose change** | Rewrap body into `Horizontal#config-main` (nav + per-section panels); nav-change handler; toggle `.hidden`; keep all ids + dismiss-dict shape. |
| `riftor/safety/permissions.py` | **None (CSS-only restyle)** | Restyle via tcss against existing ids. No Python edit (hardcoded hexes accepted). |
| `riftor/tui/widgets.py` | **None (CSS-only restyle)** | `CommandDropdown` restyle is entirely in tcss; no compose() change. |
| `riftor/config.py`, `riftor/tui/app.py`, `riftor/tui/theme.py` | **None** | No new theme vars; `app.py` save path unaffected (dismiss-dict unchanged). |

---

## 8. Testing / Verification

The suite runs **offline**; UI behavior is exercised by `dev/smoke.py` (drives the real Textual app headlessly).

- **Smoke test** (`uv run python dev/smoke.py`): extend to open `/config`, switch sidebar sections, and confirm fields remain query-able + Save returns the expected dict. This directly guards the §3.3 "all panes mounted" constraint.
- **Unit**: any existing config/permission tests must still pass unchanged (the dismiss-dict shape and decision strings are unchanged — that's the contract).
- **Manual / `/run`**: visually verify the sidebar, the restyled permission prompt, and the dropdown across at least one dark theme (`rift`) and one light theme (`dawn`/`paper`) to confirm the shared variables read correctly in both.
- **CI gates**: `make check` (lint → typecheck → test → smoke) must pass.

---

## 9. Out of Scope (explicit)

- The main chat surface (user/assistant/tool bubbles, status bar, prompt box) — that's the larger "full track-1 sweep," a separate follow-up.
- Theme-driving the confirm modal's inline Rich hex colors (deferred; see §4).
- A header/divider element inside the command dropdown (§5).
- Any agent-loop / architecture change (Track 2, noted in memory).
- New theme variables.

---

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Sidebar removes off-screen fields → `query_one` raises at Save | **Mandatory**: all panes stay mounted, toggle `.hidden` only (§3.3). Guarded by extended smoke test. |
| Splitting shared CSS selectors accidentally changes the confirm modal | Split deliberately; verify both modals visually after the CSS reorg. |
| Live theme preview desyncs | No new theme vars introduced, so `_apply_theme` stays correct by construction. |
| Initial focus lands in a hidden pane | Keep Model as the default-visible section (it holds `#cfg-provider`), or retarget `on_mount` focus to the nav. |
| Filled-button colors clash in light themes (`dawn`/`paper`) | Use existing palette vars (which are theme-aware) and verify in a light theme during manual check. |
