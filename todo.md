# riftor — build roadmap

> An open-source offensive-security AI agent that lives in your terminal.
> Find the rift. Open it. Cross through.

## What riftor is
A Python TUI pentest assistant. Full-screen [Textual](https://textual.textualize.io/)
interface, [litellm](https://docs.litellm.ai/) for cloud + local (Ollama) models,
hand-rolled agent loop. Its spine is the **RIFT** methodology engine.

## Locked decisions
- **Name:** `riftor`  (repo: https://github.com/Estudely/riftor)
- **Language:** Python 3.11+
- **TUI:** Textual (full-screen)
- **LLM layer:** litellm (own agent loop), cloud + local Ollama
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

### Phase 2 — Agent loop + core tools
- [ ] Tool ABC (schema + execute + permission level)
- [ ] Core tools: bash, read, write, edit, grep, glob, webfetch
- [ ] Tool-calling agent loop (multi-turn)
- [ ] Permission prompts for dangerous ops
- [ ] Audit log of every command

### Phase 3 — RIFT specialization
- [ ] Scope manager + enforcement (in-scope targets only)
- [ ] R·I·F·T stage tracking in engagement state
- [ ] Security tool wrappers: nmap, httpx, ffuf, nuclei, subfinder
- [ ] Engagement state DB (sqlite): hosts/ports/services/findings
- [ ] Offensive system prompt wired to methodology

### Phase 4 — Reporting + polish
- [ ] Findings -> markdown/PDF report (CVSS + evidence)
- [ ] Session persistence + resume
- [ ] Config screen
- [ ] Verify local-model (Ollama) path end-to-end
- [ ] Theme variants (Void / Fracture / Singularity)

### Phase 5 — Distribution + community
- [ ] Package for `uv tool install` / `pipx` / Docker
- [ ] Verify PyPI name `riftor` availability
- [ ] Docs + demo
- [ ] CONTRIBUTING, CI, issue templates
- [ ] Launch

---

## Environment notes
- uv 0.11.14, Python 3.12.3 at /usr/bin/python3
- Ollama running on :11434, model available: `kimi-k2.6:cloud`
- No cloud API keys set -> Phase 1 defaults to local Ollama
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
    provider.py          litellm streaming wrapper (cloud + Ollama)
    context.py           conversation history
    prompts/system.md    offensive persona + RIFT methodology
  tools/        (Phase 2)
  engagement/   (Phase 3)
  safety/       (Phase 2-3)
```
