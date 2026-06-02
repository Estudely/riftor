# riftor

> An open-source offensive-security AI agent that lives in your terminal.
> **Find the rift. Open it. Cross through.**

riftor is a Python TUI pentest assistant: a full-screen [Textual](https://textual.textualize.io/)
interface backed by [litellm](https://docs.litellm.ai/), organised around the
**RIFT** methodology — **R**econ → **I**ntrusion → **F**oothold → **T**akeover.

It's **cloud-first** (Anthropic, OpenAI, OpenRouter, …) for the strongest agent
behaviour, with local [Ollama](https://ollama.com/) supported as an option.

> **Status: Phase 2.** The agent can use tools (bash/read/write/edit/grep/glob/
> webfetch) with permission prompts and an audit log. Scope enforcement and the
> engagement engine are next — see [`todo.md`](./todo.md).

## Requirements
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- A model — set one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`
  (or run a local Ollama server)

## Run
```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, etc.
uv sync
uv run riftor
```
On first launch riftor writes a config file and picks a default model from your
environment keys (a cloud provider). If no key is set but an Ollama server is
running, it falls back to that.

```bash
uv run riftor --config   # show the config file path
uv run riftor --version
```

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

## Commands
| Command | Action |
|---|---|
| `/help` | show commands |
| `/clear` | clear the conversation (`Ctrl+L`) |
| `/model [name]` | show or switch the model |
| `/stage [R\|I\|F\|T]` | show or set the RIFT stage |
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
