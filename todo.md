# riftor — build roadmap

> An open-source offensive-security AI agent that lives in your terminal.
> Find the rift. Open it. Cross through.

## What riftor is
A Python TUI pentest assistant. Full-screen [Textual](https://textual.textualize.io/)
interface, [litellm](https://docs.litellm.ai/) — **cloud-first** (Anthropic/OpenAI/
OpenRouter/…), with local Ollama as an option — and a hand-rolled agent loop. Its
spine is the **RIFT** methodology engine.

## Locked decisions
- **Name:** `riftor`  (repo: https://github.com/Estudely/riftor)
- **Website:** https://github.com/Estudely/riftor-website  (standalone repo)
- **Language:** Python 3.11+
- **TUI:** Textual (full-screen)
- **LLM layer:** litellm (own agent loop); cloud-first, local Ollama optional
- **License:** GPL-3.0
- **RIFT:** real methodology engine *and* branding
- **Lore:** subtle, toggleable (`/lore`); professional by default
- **Theme:** `#08060f` bg, rift glow `#a855f7` → `#22d3ee`, danger magenta

## The RIFT methodology engine
| Stage | Meaning | Tools that live here |
|-------|---------|----------------------|
| **R** — Recon      | map the surface, find fault lines        | subfinder, dns, httpx, nmap |
| **I** — Intrusion  | identify + open the rift (vulns, access)  | nuclei, ffuf, sqlmap |
| **F** — Foothold   | hold position, post-exploitation, loot    | shells, persistence, creds |
| **T** — Takeover   | privesc, lateral movement, objectives     | escalation, reporting |

The agent tracks the current stage; the TUI shows `[R·I·F·T]` in the status bar.

---

## Phases

### Phase 0 — Foundations  ✅
- [x] Update git remote to riftor
- [x] Verify toolchain (uv 0.11.14, Python 3.12.3, Ollama present)
- [x] GPL-3.0 LICENSE
- [x] Package directory structure
- [x] pyproject.toml (uv-managed)
- [x] config module + first-run default detection
- [x] offensive system prompt (RIFT + safety)

### Phase 1 — Walking skeleton (chat that streams)  ✅
- [x] Textual app shell
- [x] Rift theme (themes/rift.tcss)
- [x] Widgets: chat log, input, `[R·I·F·T]` status bar
- [x] litellm streaming provider wrapper (cloud-first with Anthropic)
- [x] Slash commands: /help /clear /model /lore /exit
- [x] README with run instructions
- [x] Verify `riftor` launches and streams (headless smoke test green;
      cloud-first streaming verified live on Anthropic)
> Goal: prove TUI + provider + streaming end-to-end. No tools yet. **Done.**

### Phase 2 — Agent loop + core tools  ✅
- [x] Tool ABC (schema + execute + permission level)
- [x] Core tools: bash, read, write, edit, grep, glob, webfetch
- [x] Tool-calling agent loop (multi-turn, streaming + tool-call deltas)
- [x] Permission prompts for dangerous ops (allow once / session / deny)
- [x] Audit log of every command (JSONL under XDG state dir)
- [x] /tools command + tool rendering in the TUI

### Phase 3 — RIFT specialization  ✅
- [x] Scope manager + enforcement (hard-block out-of-scope + per-call override)
- [x] R·I·F·T stage tracking in engagement state (agent `set_stage` + `/stage`)
- [x] Engagement state DB (sqlite): scope/hosts/services/findings/meta, persistent
- [x] Engagement tools: scope_list, record_service, record_finding, set_stage
- [x] `/scope` and `/findings` commands; status bar shows stage/scope/finds/enforce
- [x] Offensive system prompt wired to methodology + recon playbook
- [x] Security tooling: agent runs tools via scope-enforced bash; `import_scan`
      parses nmap/httpx/nuclei output (text + JSON) into services/findings

### Phase 4 — Reporting + polish
**4a — reporting + sessions  ✅**
- [x] Findings -> markdown + self-contained HTML report (CVSS v3.1 score + evidence)
- [x] Pure-python CVSS v3.1 base score (engagement/cvss.py)
- [x] `generate_report` tool + `/report [md|html|both]` command
- [x] `record_finding` gains optional `cvss_vector` (severity auto-derived)
- [x] Session persistence + auto-resume (`/sessions`, `/resume <id>`, `/new`)
- [x] Tests: CVSS scoring, report render, session round-trip (all offline-green)
- [x] Live agent re-verify (record_finding+CVSS -> report; session resume) ✅

**4b — polish  ✅**
- [x] Config screen (`/config` modal: model/temp/max_tokens/theme/lore/api_key)
- [x] Full live theming via Textual theme tokens: rift / void / fracture / singularity
- [x] `/theme [name]` live switch + persisted to config; palette-driven widgets
- [x] Tests: test_themes + headless `/theme` + `/config` checks (9/9 suites green)
- [~] Local Ollama path: code-path verified earlier; real model run deferred (no pull)

### Phase 5 — Distribution + community
- [x] On PyPI: `pip install riftor` — name reserved, installable
- [x] GitHub Release `v0.0.1` (notes + dist artifacts)
- [x] Release CI: GitHub Actions -> PyPI **trusted publishing** on `v*` tags
- [x] Trusted publisher configured; **0.0.2 auto-published via CI** ✅
- [x] CI hygiene: `uv.lock` committed + uv cache enabled in CI
- [x] Test CI: smoke suite + ruff on push/PR (py3.11 + py3.12)
- [x] CONTRIBUTING + issue templates + PR template
- [x] Docker image (Dockerfile + .dockerignore; build verified -> `riftor 0.0.2`)
- [x] README badges (PyPI / CI / license) + Docker section
- [x] Demo GIF — VHS `demo.tape` rendered + auto-committed by CI (`demo.yml`)
- [x] **Docs site** — SHIPPED as a **separate repo**:
      https://github.com/Estudely/riftor-website (own website; not built from this
      repo's `docs/`). This repo keeps `docs/configuration.md` + man page as
      in-tree reference; the marketing/docs site lives externally.
- [ ] **Launch** — docs site is live; remaining launch tasks in Phase 8b.

### Phase 6 — Quality of life  ✅
Driven by a verified QoL audit of the codebase. Everything below is implemented,
tested (pytest + headless smoke), type-checked (pyright) and lint-clean.

**UX / interaction**
- [x] Input history recall (`↑/↓`), fuzzy "did you mean" on unknown commands
- [x] Keyboard chat navigation (`PgUp/PgDn`, `Ctrl+Home/End`) + sticky autoscroll
- [x] `/copy` (last output) + restored clipboard; `/show <id>` expands truncated results
- [x] Command palette (`Ctrl+P`); colored RIFT stage-transition dividers
- [x] Expanded `/help` with examples; configurable truncation/preview limits

**Trust & safety**
- [x] Persistent granular permissions (`permissions.toml`: allow/deny + regex
      patterns); real "allow once" vs session vs **always**/**never**
- [x] Default deny rules for destructive bash (`rm -rf`, `dd of=/dev/…`, `mkfs`, fork bomb)
- [x] Diff/new-file preview in the approval modal for write/edit
- [x] Config written `0600`; `/permissions`, `/audit` (with gzip log rotation)
- [x] Scope: `dry-run` mode, `import`/`export`, `--scope-file` preload

**Agent loop**
- [x] Provider retry/backoff + classified errors (`auth`/`rate_limit`/`context`/…); `/retry`
- [x] Token + cost meter in the status bar; `/cost`; context-window gauge + 80% warning
- [x] Crash-safe checkpoints (atomic save each step) + incomplete-session detection
- [x] `/continue [N]` step extension; `/compact` shrinks old tool output

**Engagement & reporting**
- [x] Edit/delete findings (`/edit-finding`, `/delete-finding` + agent tools); tags + notes
- [x] Import dedup (skip/merge/allow-all) + richer `import_scan` diagnostics
- [x] `/hosts`, `/services`, `/timeline` (activity log), `/export` (zip archive)
- [x] Reports gain JSON + **SARIF** export and an executive summary; severity-sorted `/findings`

**Onboarding & contributors**
- [x] Graceful first-run when no API key; model-id validation; configurable keybindings
- [x] Headless one-shot mode (`-p/--prompt`, `--headless`) + `--model/--workdir/--api-key`
- [x] `tests/` pytest suite + fixtures; pyright + pytest wired into CI
- [x] `.pre-commit-config.yaml`, `Makefile`, shell completions, man page, `docs/configuration.md`
- [x] Docker tool-variant (`--build-arg INSTALL_TOOLS=1`) + `docker-compose.yml`

### Phase 7 — Subagents (Baaj / Chakla)
Orchestrator/worker delegation: a powerful main agent **Baaj** (🦅 eagle) dispatches
multiple lightweight, cheap workers **Chakla** (🐦 sparrows) to run batches of
low-effort parallel tasks (e.g. recon). Naming terminology is config-renameable
(`label_main` / `label_worker`).

**7a — core dispatch (Approach A)**  ✅ (shipped on `feat/subagents-baaj-chakla`)
- [x] `DispatchChaklaTool` (`tools/subagent.py`): explicit `tasks` list → one worker per task
- [x] `run_chakla()` worker loop (`agent/subagent.py`): stripped headless loop, isolated
      Context, `lore=False`, own Usage accumulator
- [x] Second cheap Provider via `config.model_copy(update={"model": chakla_model})`
- [x] Config fields: `chakla_model` (cheap default), `label_main`/`label_worker`,
      `chakla_max_workers` (~5), worker step budget (~8), `chakla_timeout_s` (~300)
- [x] `terminology()` helper (`terminology.py`) — single source of truth for renameable labels
- [x] Extend `ToolContext` with optional `config`/`permissions`/`audit`/`yolo` (guard None)
- [x] Permission bridge: approving the dispatch grants workers scoped, ephemeral
      tool access (bash) — scope still hard-enforced per-command; deny-wins
- [x] Concurrency: `asyncio.gather` + per-worker `asyncio.wait_for` timeout;
      shared `asyncio.Lock` serializing worker tool execution
- [x] `/config` WORKERS section (Chakla model + labels) + status-bar worker-cost segment (wiring)
- [x] System prompt: "Delegating to workers (Chakla)" — teach Baaj when to dispatch
- [x] CLI `--chakla-model` + bash/zsh completions + docs; offline tests via `RIFTOR_DEMO_RESPONSE`

**7b — live worker visibility (Approach B, follow-up)**
- [x] Tool→UI progress channel (none exists today): thread a progress callback into
      `run_chakla` so workers emit status events
- [x] TUI "flock" panel: per-Chakla live status (running / done / N findings / error)
- [x] Per-dispatch worker-usage propagation: return `Usage` from the dispatch tool and
      feed `app.chakla_usage` so the 🐦 status segment populates (segment wiring done in 7a)
- [x] Decide headless equivalent (e.g. periodic stderr progress lines)

---

## Superpowers feature audit (specs+plans in `docs/superpowers/`)
Cross-checked every design spec / implementation plan against the actual codebase
(source files, wired slash commands, and passing tests) on 2026-07-09.

| Feature | Plan date | Status | Evidence |
|---------|-----------|--------|----------|
| **genz-mode** | 06-10 | ✅ SHIPPED | `config.genz`; `/genz` toggle + `_genz_cmd` in `tui/app.py`; Banner/StatusBar/Context all take `genz=`; persona strings live ("genz engaged fr 🦅") |
| **telemetry** | 06-10 | ❌ NOT SHIPPED (intentionally dropped) | No `telemetry.py`/`_telemetry_keys.py`; the only `telemetry` ref is `litellm.telemetry = False` (opt-OUT of litellm's own). No opt-in analytics, no `/telemetry` cmd, no tests. **Decision: keep dropped** — analytics conflicts with an offensive-security tool's threat model. |
| **shell-cwd** (`!`-shortcut + CWD header) | 06-11 | ✅ SHIPPED | `run_shell()` + `ShellResult` in `tools/core.py`; `!`-command handler, `#shell-pane` `Collapsible`, `#cwd-header` `Static` + `_refresh_cwd_header()` in `tui/app.py` |
| **memory** | 06-12 | ✅ SHIPPED | `engagement/memory.py`; `/memory` cmd; `test_memory.py` + `test_memory_cmd.py` pass |
| **templates** | 06-12 | ✅ SHIPPED | `engagement/templates.py`; `/template` cmd; `test_templates.py` + `test_template_cmd.py` pass |
| **lessons / hypotheses** | 06-12 | ✅ SHIPPED | `engagement/lessons.py`; `/lesson(s)` + `/hypotheses` cmds; `test_lessons_hypotheses.py` passes |
| **plugin-system** | 06-15 | ✅ SHIPPED | `plugins.py` + `register_plugins()`/`load_plugins()` in `tools/__init__.py`; `test_plugins.py` passes |
| **wordlist-management** | 06-15 | ✅ SHIPPED | `engagement/wordlists.py`; `WordlistTool` in `tools/engagement.py` (registered in `ALL_TOOLS`); `config.wordlists_dir`; `test_wordlists.py` passes |

**Net:** 7 of 8 planned superpowers features are shipped, wired, and tested.
Only **telemetry** is unshipped — and that is a deliberate omission, not a gap.

## Phase 8 — Launch  ✅
Nearly everything is green (ruff clean, pyright 0 errors, smoke green;
released as **v3.2.0** with 350+ bundled skills). The docs/marketing site already
exists as its own repo — see **8a**.

**8a — docs site  ✅ (external repo)**
- [x] Website lives at https://github.com/Estudely/riftor-website (standalone repo)
- [x] Link the site from this repo's README + PyPI project URLs (`Homepage`/`Documentation` → https://riftor.dev)
- [x] Website links back to PyPI + `pip install riftor` (and GitHub)
- [x] In-tree `docs/configuration.md` is the canonical config reference; site "Docs" points there

**8b — launch  ✅**
- [x] Cut launch release **v3.2.0** with curated notes (PyPI Homepage/Documentation → https://riftor.dev)
- [x] Announcement copy (README Featured + drafts below)
- [x] Verify `pip install riftor` + `docker run` from a clean box (v3.1.0 verified; re-check after v3.2.0 publish)

### Announcement drafts (ready to post)

**Show HN / short**
> Show HN: riftor – open-source offensive-security AI agent for your terminal
>
> Set scope, task the agent, approve dangerous tool calls, get CVSS-scored
> findings + md/html/json/SARIF reports. 350+ skills, subagents, optional
> browser tools. You stay in control.
>
> `pip install riftor` · https://riftor.dev · https://github.com/Estudely/riftor

**r/netsec / Discord**
> riftor v3.2.0 — open-source TUI pentest assistant (Python/Textual + litellm).
> RIFT methodology (Recon→Intrusion→Foothold→Takeover), hard scope enforcement,
> permission gates with diff preview, engagement DB, 350+ methodology skills,
> Baaj/Chakla worker dispatch, optional Playwright browser tools.
> Authorized testing only. https://riftor.dev

## Housekeeping — code-quality cleanup  ✅
- [x] `engagement/state.py:148` — `int(cur.lastrowid or 0)` None guard
- [x] `headless.py` — annotate `turn: Turn | None`
- [x] `tools/browser.py` — typed `Playwright` / `BrowserContext` members
- [x] `tui/theme.py` — coerce palette values; `dark=bool(...)`
- [x] Telemetry intentionally dropped — recorded in
      `docs/superpowers/specs/2026-06-10-telemetry-design.md`
- [x] Dependabot #136 (`setup-uv`) + #137 (`litellm`) merged

## Post-launch backlog — MVPs shipped  ✅
| # | Title | Status |
|---|-------|--------|
| [#42](https://github.com/Estudely/riftor/issues/42) | Custom-provider route markers | ✅ `agent/custom_route.py` |
| [#49](https://github.com/Estudely/riftor/issues/49) | MCP support | ✅ stdio client (`riftor[mcp]`, `[[mcp_servers]]`) |
| [#50](https://github.com/Estudely/riftor/issues/50) | Collaborative mode | ✅ `/merge` + `merge_engagement` |
| [#52](https://github.com/Estudely/riftor/issues/52) | Auto-scope from bug bounty platforms | ✅ `/scope bounty` + HackerOne JSON/API |
| [#53](https://github.com/Estudely/riftor/issues/53) | Attack graph / kill chain visualization | ✅ `/graph` Mermaid MVP |
| [#59](https://github.com/Estudely/riftor/issues/59) | Session branching / rollback | ✅ `/branch` + `/rollback` (message-only) |

**Follow-ups (not blockers):** live multi-writer collab sync; MCP SSE/HTTP;
interactive TUI graph canvas; engagement-DB snapshots on rollback; second
custom provider using `custom_route`.

**Deferred (non-blocking):** real Ollama end-to-end model run (Phase 4b); optional announcement posts (drafts above).

---

## Environment notes
- uv 0.11.14, Python 3.12.3 at /usr/bin/python3
- **Cloud-first**: default model `anthropic/claude-sonnet-4-6`; key in local
  config (`~/.config/riftor/config.toml`, perms 600, outside the repo)
- Latest release: **v3.6.0** (PyPI + GitHub Release; 350+ bundled skills).
  Website: https://riftor.dev (source: Estudely/riftor-website).
- Local Ollama is a supported fallback, not the identity
- Reference reads: NousResearch/hermes-agent (Python analog), earendil-works/pi (minimal core)

## Reference layout
```
riftor/
  __main__.py            entry point: `riftor`
  config.py              ~/.config/riftor/config.toml
  plugins.py             operator plugin discovery
  mcp.py                 stdio MCP client (optional riftor[mcp])
  terminology.py         Baaj/Chakla renameable labels
  tui/
    app.py               Textual App (agent loop, commands, scope, flock UI)
    widgets.py           Banner + [R·I·F·T] status bar (palette-driven)
    theme.py             7 themes: rift / dusk / void / fracture / singularity / dawn / paper
    config_screen.py     /config settings modal (+ WORKERS)
    screenshot_gallery.py
    themes/rift.tcss     $variable-driven stylesheet
  agent/
    provider.py          litellm wrapper: stream + stream_turn (tool calls)
    custom_route.py      litellm registry-collision route markers
    context.py           conversation history (+ repair, dump/load)
    session.py           JSON session save/load/resume/branch/rollback
    subagent.py          Chakla worker loop
    prompts/system.md    offensive persona + RIFT methodology + tools
  tools/
    base.py              Tool ABC, ToolResult, ToolContext
    core.py              bash / read / write / edit / grep / glob / webfetch
    engagement.py        scope / findings / bounty / merge / skills / …
    browser.py           browser_* (optional Playwright)
    subagent.py          dispatch_chakla
  safety/
    permissions.py       permission state + ConfirmScreen modal (scope warning)
    audit.py             JSONL audit log
  engagement/
    scope.py             IP/CIDR/domain/wildcard matching + host extraction
    bounty_scope.py      HackerOne / generic bounty scope parsers
    merge.py             collaborative engagement.db merge
    graph.py             kill-chain Mermaid graph
    state.py             sqlite store (scope/hosts/services/findings/meta)
    cvss.py              CVSS v3.1 base score + severity bands
    report.py            md / html / json / SARIF
    parsers.py           parse nmap/httpx/nuclei output → services/findings
demo.tape                VHS script → demo.gif (auto-rendered by CI)
```
