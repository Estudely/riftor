# riftor

> An open-source AI agent for authorized bug bounty and penetration testing in your terminal.

[![PyPI](https://img.shields.io/pypi/v/riftor)](https://pypi.org/project/riftor/)
[![CI](https://github.com/Estudely/riftor/actions/workflows/ci.yml/badge.svg)](https://github.com/Estudely/riftor/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](https://github.com/Estudely/riftor/blob/main/LICENSE)

**🌐 Website:** https://riftor.dev · **Latest:** [v4.0.0](https://github.com/Estudely/riftor/releases/tag/v4.0.0)

![riftor demo](https://raw.githubusercontent.com/Estudely/riftor/main/demo.gif)

### Featured — v4.0.0
An open-source AI agent for **authorized** bug bounty and penetration testing in
your terminal. Set scope, task the agent, approve the dangerous bits, and walk
out with CVSS-scored findings and md/html/json/SARIF reports. **350+**
methodology skills, parallel **workers**, optional browser + MCP tools,
bounty-scope import, attack graphs, and hard scope enforcement — you stay in
control. `pip install riftor` · https://riftor.dev

**riftor** puts an AI copilot at your terminal for offensive security. Talk to it
like a teammate — tell it to scan a subnet, dig into a suspicious service, or
write up findings — and it runs the tools, respects your scope, and builds the
report as you go. Progress is tracked with an **OWASP/PTES methodology
checklist** (not a fixed kill-chain stage). Powered by any major LLM through
[litellm](https://docs.litellm.ai/) and wrapped in a full-screen
[Textual](https://textual.textualize.io/) interface.

> **What's inside:** a streaming agent with retry/backoff + token/cost metering,
> **persistent granular permissions** (allow/deny rules, diff preview before
> write/edit), a **scope guardrail** (enforce / dry-run / import-export), an
> OWASP/PTES **methodology checklist** with auto-tick on relevant tool runs, a
> per-engagement findings store (edit/tag/dedup/CVSS) with reports in
> **md/html/json/sarif**, and crash-safe sessions. It also keeps **cross-session
> memory** (lessons it carries between engagements), tracks **hypotheses** (open
> leads, so it never re-tests a refuted one), runs a **self-critique pass** over
> findings before you report, and has an **anti-loop circuit breaker** that stops
> it spinning on a repeated call. **Workers** fan out independent recon in
> parallel via `dispatch_worker`. Plus input history + command palette, headless
> one-shot mode, Docker, and pytest + types in CI. See
> [`todo.md`](https://github.com/Estudely/riftor/blob/main/todo.md) for the
> roadmap and [`docs/`](https://github.com/Estudely/riftor/tree/main/docs) for
> configuration.

## Install
```bash
pip install riftor                 # or: uv tool install riftor / pipx install riftor
pip install 'riftor[browser]'      # + Playwright, for the browser_* tools (SPA recon)
```
Requires Python 3.11+ and a model — set one of `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY` (or run a local [Ollama](https://ollama.com/) server).
The `[browser]` extra is optional (it adds ~hundreds of MB of Chromium binaries);
without it the browser tools just print an install hint and everything else works.

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, etc.
riftor                                 # launch the TUI
riftor --config                        # show the config file path
riftor --version
riftor --model openai/gpt-4o           # override the model for this run
riftor --worker-model anthropic/claude-haiku-4-5-20251001  # cheaper worker model
riftor --workdir ./engagement          # set the engagement directory
riftor --scope-file scope.txt          # preload scope targets
riftor -p "enumerate 10.0.0.5"         # headless one-shot (also reads stdin)
riftor --doctor                        # check which recon tools are installed
```

On first launch riftor writes a config file and picks a default model from your
environment keys (cloud-first); if no key is set but an Ollama server is running,
it falls back to that.

### From source
```bash
git clone https://github.com/Estudely/riftor && cd riftor
uv sync && uv run riftor
```

### Docker
```bash
docker build -t riftor .
docker run -it --rm -e ANTHROPIC_API_KEY -v "$PWD:/work" riftor
```
The image is minimal (no `nmap`/`httpx`/etc.). For full recon tooling, build the
bundled variant (`docker build --build-arg INSTALL_TOOLS=1 -t riftor:full .`), run
riftor on a host that has the tools, or extend the image. Missing tools aren't
fatal — the agent sees the failed command and adapts; run `/doctor` (or
`riftor --doctor`) to see which tools are on `PATH` up front.

## Configure
`~/.config/riftor/config.toml`:
```toml
[riftor]
model = "anthropic/claude-sonnet-4-6"  # any litellm model id
# api_key = "sk-..."                   # prefer the provider's env var
temperature = 0.3
max_tokens = 2048
theme = "rift"

# Workers (parallel subagents on a cheaper model):
# worker_model = "anthropic/claude-haiku-4-5-20251001"  # empty => reuse main model
# worker_max_parallel = 5
# worker_timeout_s = 300

# Local option (Ollama):
# model = "ollama_chat/llama3.1"
# api_base = "http://localhost:11434"
```

## Workflow
```text
1. Set scope        /scope add 10.0.0.0/24 example.com
2. Task the agent   "enumerate the web host and look for low-hanging fruit"
                    → it runs recon tools via bash (you approve), records
                      services/findings, and ticks the methodology checklist
3. Review           /findings · /methodology
4. Report           /report            → .riftor/reports/report-*.md and .html
```
Out-of-scope targets are **blocked** (with an explicit per-call override). State
lives in `.riftor/` per working directory; sessions auto-save and resume.

## Methodology checklist
riftor tracks OWASP/PTES-style testing progress per engagement — recon,
authentication, authorization, injection, and more. The agent uses
`list_methodology` / `check_methodology`; relevant tool runs auto-tick items
(e.g. `nmap` → Port scanning). View progress with `/methodology` or check items
manually with `/methodology check <item>`. The status bar shows `done/total`.

## Workers
The lead agent can fan out independent tasks in parallel via `dispatch_worker`
(one worker per task string). Built-in roles: **recon**, **scout**, **tester**,
**analyst**, **exploiter**. Workers share scope enforcement and the engagement
database; tune concurrency with `worker_max_parallel`, model with `worker_model`,
and wall-clock limit with `worker_timeout_s`. List roles with `/workers`.

Drop custom worker specs in `~/.config/riftor/workers/<name>.md` (YAML front
matter + system prompt). See [`docs/configuration.md`](docs/configuration.md).

## Commands
| Command | Action |
|---|---|
| `/help` | show commands |
| `/clear` | clear the conversation (`Ctrl+L`) |
| `/retry` · `/continue [N]` · `/compact` | re-run last turn · extend steps · free context |
| `/copy` · `/show <id>` · `/cost` | copy last output · expand a result · token/cost total |
| `/model [name]` · `/theme [name]` | switch model / theme |
| `/scope [add\|out\|rm <t>\|clear\|on\|off\|dry\|import <f>\|export [f]]` | manage scope |
| `/methodology` · `/methodology check <item>` | OWASP/PTES checklist progress |
| `/workers` | list built-in and custom worker roles |
| `/findings` · `/finding <id>` | list (severity-sorted) / show one |
| `/edit-finding <id> sev=high tags=…` · `/delete-finding <id>` | triage findings |
| `/review` | self-critique findings for false-positive signals before reporting |
| `/hypotheses` | list tracked hypotheses (open leads the agent is chasing) |
| `/lesson <text>` · `/lessons` | teach a durable cross-session lesson · list them |
| `/hosts` · `/services` | discovered infrastructure |
| `/report [md\|html\|json\|sarif\|both\|all]` | write a report to `.riftor/reports/` |
| `/timeline` · `/export` | engagement activity log · archive the engagement |
| `/permissions` · `/audit` | review allow/deny rules · recent tool-call log |
| `/doctor` | check which external recon tools (nmap/httpx/…) are installed |
| `/sessions` · `/resume <id>` · `/new` | manage saved sessions |
| `/config` · `/tools` · `/exit` | settings · tools · quit |

`↑/↓` recall previous prompts · `PgUp/PgDn` scroll · `Ctrl+P` command palette ·
`Esc` cancels a running response. Dangerous tools (bash/write/edit) prompt for
approval (with a **diff preview**); `rm -rf`/`dd` and friends are denied by
default; every tool call is written to an audit log. See
[`docs/configuration.md`](https://github.com/Estudely/riftor/blob/main/docs/configuration.md) for all settings.

> ⚠️ **`--i-know-what-i-am-doing-give-me-full-access`** (YOLO mode) bypasses
> *every* guardrail — no permission prompts, no scope enforcement, no step
> limit. Only use it on a target you fully control and have explicit
> authorization for.

## Migrating from v3
v4.0.0 removes RIFT stage tracking, lore/genz personas, and Baaj/Chakla naming.
Config keys are renamed on load (your file is not rewritten automatically):

| v3 key | v4 key |
|---|---|
| `chakla_model` | `worker_model` |
| `chakla_max_workers` | `worker_max_parallel` |
| `chakla_timeout_s` | `worker_timeout_s` |

Removed (ignored on load): `lore`, `genz`, `label_main`, `label_worker`.

| v3 | v4 |
|---|---|
| `/stage`, `set_stage` tool | `/methodology`, `list_methodology` / `check_methodology` |
| `dispatch_chakla` tool | `dispatch_worker` |
| `--chakla-model` CLI flag | `--worker-model` (alias still accepted) |

Full breaking-change notes: [`docs/RELEASE_NOTES_v4.0.0.md`](docs/RELEASE_NOTES_v4.0.0.md).

## Use responsibly
riftor is for **authorized** security testing only. You are responsible for
having explicit, written permission for any system you assess.

## License
[GPL-3.0](https://github.com/Estudely/riftor/blob/main/LICENSE).
