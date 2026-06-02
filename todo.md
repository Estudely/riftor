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
- **Language:** Python 3.11+
- **TUI:** Textual (full-screen)
- **LLM layer:** litellm (own agent loop); cloud-first, local Ollama optional
- **License:** GPL-3.0
- **RIFT:** real methodology engine *and* branding
- **Lore:** subtle, toggleable (`/lore`); professional by default
- **Theme:** void bg `#0a0a12`, rift glow `#a855f7` -> `#22d3ee`, danger magenta

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
- [x] litellm streaming provider wrapper (default: local Ollama)
- [x] Slash commands: /help /clear /model /lore /exit
- [x] README with run instructions
- [x] Verify `riftor` launches and streams (headless smoke test green;
      litellm→Ollama path confirmed — needs a usable model: a free local
      Ollama pull, an Ollama subscription, or a cloud API key)
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
- [~] Security tool wrappers: agent drives nmap/httpx/ffuf/nuclei/subfinder/etc via
      scope-enforced bash + records results; dedicated output parsers deferred

### Phase 4 — Reporting + polish
**4a — reporting + sessions  ✅**
- [x] Findings -> markdown + self-contained HTML report (CVSS v3.1 score + evidence)
- [x] Pure-python CVSS v3.1 base score (engagement/cvss.py)
- [x] `generate_report` tool + `/report [md|html|both]` command
- [x] `record_finding` gains optional `cvss_vector` (severity auto-derived)
- [x] Session persistence + auto-resume (`/sessions`, `/resume <id>`, `/new`)
- [x] Tests: CVSS scoring, report render, session round-trip (all offline-green)
- [~] Live agent re-verify pending a fresh API key (old key rotated -> 401)

**4b — polish (next):**
- [ ] Config screen (`/config` modal)
- [ ] Theme variants (Void / Fracture / Singularity) + `/theme`
- [ ] Verify local Ollama path (needs a pullable local model)

### Phase 5 — Distribution + community
- [ ] Package for `uv tool install` / `pipx` / Docker
- [ ] Verify PyPI name `riftor` availability
- [ ] Docs + demo
- [ ] CONTRIBUTING, CI, issue templates
- [ ] Launch

---

## Environment notes
- uv 0.11.14, Python 3.12.3 at /usr/bin/python3
- **Cloud-first**: default model `anthropic/claude-sonnet-4-6`; key in local
  config (`~/.config/riftor/config.toml`, perms 600, outside the repo)
- Local Ollama is a supported fallback, not the identity (`kimi-k2.6:cloud`
  present locally but is subscription-gated)
- Reference reads: NousResearch/hermes-agent (Python analog), earendil-works/pi (minimal core)

## Reference layout
```
riftor/
  __main__.py            entry point: `riftor`
  config.py              ~/.config/riftor/config.toml
  tui/
    app.py               Textual App
    widgets.py           chat log, input, [R·I·F·T] status bar
    themes/rift.tcss     void bg + violet->cyan glow
  agent/
    provider.py          litellm wrapper: stream + stream_turn (tool calls)
    context.py           conversation history (+ tool messages)
    prompts/system.md    offensive persona + RIFT methodology + tools
  tools/                 base + core + engagement (scope_list/record_*/set_stage)  ✅
  safety/                audit log + permission modal (+ scope warning)  ✅
  engagement/            scope.py + state.py (sqlite) + Engagement facade  ✅
```
