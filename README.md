# riftor

> An open-source offensive-security AI agent that lives in your terminal.
> **Find the rift. Open it. Cross through.**

riftor is a Python TUI pentest assistant: a full-screen [Textual](https://textual.textualize.io/)
interface backed by [litellm](https://docs.litellm.ai/) (cloud **or** local models),
organised around the **RIFT** methodology — **R**econ → **I**ntrusion → **F**oothold → **T**akeover.

> **Status: Phase 1 (walking skeleton).** A themed chat that streams from a model.
> Tooling, scope enforcement, and the engagement engine are on the roadmap — see
> [`todo.md`](./todo.md).

## Requirements
- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- A model: a local [Ollama](https://ollama.com/) server **or** a cloud API key
  (Anthropic / OpenAI / OpenRouter / …)

## Run
```bash
uv sync
uv run riftor
```
On first launch riftor writes a config file and picks a default model: your local
Ollama model if one is reachable, otherwise a cloud provider from your environment
keys (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`).

```bash
uv run riftor --config   # show the config file path
uv run riftor --version
```

## Configure
`~/.config/riftor/config.toml`:
```toml
[riftor]
model = "ollama_chat/llama3.1"     # any litellm model id
api_base = "http://localhost:11434" # for ollama / custom endpoints
# api_key = "sk-..."                # prefer the provider's env var
temperature = 0.3
max_tokens = 2048
theme = "rift"
lore = true
```

## Commands
| Command | Action |
|---|---|
| `/help` | show commands |
| `/clear` | clear the conversation (`Ctrl+L`) |
| `/model [name]` | show or switch the model |
| `/lore` | toggle the rift persona |
| `/exit` | quit (`Ctrl+C`) |

`Esc` cancels a running response.

## Use responsibly
riftor is for **authorized** security testing only. You are responsible for
having explicit, written permission for any system you assess.

## License
[GPL-3.0](./LICENSE).
