# riftor

> An open-source offensive-security AI agent that lives in your terminal.
> **Find the rift. Open it. Cross through.**

[![PyPI](https://img.shields.io/pypi/v/riftor)](https://pypi.org/project/riftor/)
[![CI](https://github.com/Estudely/riftor/actions/workflows/ci.yml/badge.svg)](https://github.com/Estudely/riftor/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](./LICENSE)

riftor is a Python TUI pentest assistant: a full-screen [Textual](https://textual.textualize.io/)
interface backed by [litellm](https://docs.litellm.ai/), organised around the
**RIFT** methodology — **R**econ → **I**ntrusion → **F**oothold → **T**akeover.

It's **cloud-first** (Anthropic, OpenAI, OpenRouter, …) for the strongest agent
behaviour, with local [Ollama](https://ollama.com/) supported as an option.

> **Status: early (Phase 4a).** Working agent: streaming chat, tool use with
> permission prompts + audit log, **scope enforcement**, RIFT stage tracking, a
> per-engagement findings store, and **reports** (markdown + HTML with CVSS).
> See [`todo.md`](./todo.md) for the roadmap.

## Install
```bash
pip install riftor          # or: uv tool install riftor / pipx install riftor
```
Requires Python 3.11+ and a model — set one of `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `OPENROUTER_API_KEY` (or run a local [Ollama](https://ollama.com/) server).

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, etc.
riftor                                 # launch the TUI
riftor --config                        # show the config file path
riftor --version
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
The image is minimal (no `nmap`/`httpx`/etc.). For full recon tooling, run riftor
on a host that has the tools installed, or extend the image.

## Configure
`~/.config/riftor/config.toml`:
```toml
[riftor]
model = "anthropic/claude-sonnet-4-6"  # any litellm model id
# api_key = "sk-..."                   # prefer the provider's env var
temperature = 0.3
max_tokens = 2048
theme = "rift"
lore = true

# Local option (Ollama):
# model = "ollama_chat/llama3.1"
# api_base = "http://localhost:11434"
```

## Workflow
```text
1. Set scope        /scope add 10.0.0.0/24 example.com
2. Task the agent   "enumerate the web host and look for low-hanging fruit"
                    → it runs recon tools via bash (you approve), records
                      services/findings, and advances the R·I·F·T stage
3. Review           /findings
4. Report           /report            → .riftor/reports/report-*.md and .html
```
Out-of-scope targets are **blocked** (with an explicit per-call override). State
lives in `.riftor/` per working directory; sessions auto-save and resume.

## Commands
| Command | Action |
|---|---|
| `/help` | show commands |
| `/clear` | clear the conversation (`Ctrl+L`) |
| `/model [name]` | show or switch the model |
| `/stage [R\|I\|F\|T]` | show or set the RIFT stage |
| `/scope [add\|out\|rm <t>\|clear\|on\|off]` | manage in/out-of-scope targets |
| `/findings` | list recorded findings |
| `/report [md\|html\|both]` | write a pentest report to `.riftor/reports/` |
| `/sessions` · `/resume <id>` · `/new` | manage saved sessions |
| `/tools` | list available tools |
| `/lore` | toggle the rift persona |
| `/exit` | quit (`Ctrl+C`) |

`Esc` cancels a running response. Dangerous tools (bash/write/edit) prompt for
approval; every tool call is written to an audit log.

## Use responsibly
riftor is for **authorized** security testing only. You are responsible for
having explicit, written permission for any system you assess.

## License
[GPL-3.0](./LICENSE).
