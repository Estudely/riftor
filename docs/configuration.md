# Configuring riftor

riftor reads `~/.config/riftor/config.toml` (or `$XDG_CONFIG_HOME/riftor/`). The
file is created on first run and written with `0600` perms (it may hold an API
key). Edit it directly, or use the in-app `/config` panel and `/model` / `/theme`
commands — those persist your changes back to the file.

A malformed config never blocks startup: if the file can't be parsed, riftor
falls back to detected defaults (it won't overwrite your file) and launches.

## `[riftor]` fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `model` | string | `anthropic/claude-sonnet-4-6` | Any [litellm](https://docs.litellm.ai/) model id (`provider/name`). |
| `api_base` | string | — | Legacy global endpoint override. Prefer a per-provider `[providers.<key>]` entry (see below). |
| `api_key` | string | — | Legacy global key. Prefer the provider's env var or a `[providers.<key>]` entry. |
| `temperature` | float | `0.3` | Sampling temperature, `0.0`–`2.0`. Lower = more deterministic. |
| `max_tokens` | int | `2048` | Max tokens per model response. |
| `theme` | string | `rift` | Dark: `rift` `dusk` `void` `fracture` `singularity` · Light: `dawn` `paper`. Changing it in `/config` previews live. |
| `lore` | bool | `true` | The subtle rift persona; off = strictly professional voice. |
| `max_steps` | int | `16` | Tool-call steps per task before pausing (extend live with `/continue`). |
| `max_result_chars` | int | `30000` | Cap on tool output fed back to the model. |
| `result_preview_lines` | int | `25` | Lines of a tool result shown before `…/show <id>`. |
| `rate_limit_per_min` | int | `0` | Cap model calls per minute (`0` = unlimited). |

### Example
```toml
[riftor]
model = "anthropic/claude-sonnet-4-6"
temperature = 0.3
max_tokens = 2048
theme = "rift"
lore = true
max_steps = 16
rate_limit_per_min = 0

# Local Ollama instead:
# model = "ollama_chat/llama3.1"
# api_base = "http://localhost:11434"
```

## Providers & models

Open `/config` to pick a provider and model. The **Provider** dropdown lists
Anthropic, OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Mistral, Ollama, and
Custom. Picking one prefills the **Base URL** with that provider's default and
fills the **Model** dropdown with curated suggestions. The **Custom id** field
overrides the dropdown with any litellm model id you type; the **Custom** provider
is for self-hosted / OpenAI-compatible servers.

**Fetch models** pulls the live model list from the provider's endpoint and
merges it with the curated favourites (favourites pinned first):

- OpenAI-compatible providers (OpenAI, OpenRouter, Groq, DeepSeek, Mistral,
  Custom) query `{base}/models`.
- Ollama queries `{base}/api/tags`.
- Anthropic and Gemini have no public list endpoint, so only curated suggestions
  show.

If a fetch fails (offline, bad key), riftor keeps the curated list and shows a
hint in the panel title — it never blocks you.

## API keys

riftor is cloud-first. Set one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or
`OPENROUTER_API_KEY` in your shell — the first one found picks the default model
on first launch. If none is set and an Ollama server is running, riftor uses
that. With no key at all, riftor still launches and tells you what to set; add a
key with `/config` or `export …` and switch with `/model`.

Keys you enter in `/config` are stored per provider in a `[providers.<key>]`
table (still `0600`), so you can keep credentials for several providers and
switch between them without re-entering:

```toml
[riftor]
model = "anthropic/claude-opus-4-8"

[providers.anthropic]
api_key = "sk-ant-..."

[providers.ollama]
api_base = "http://localhost:11434"
```

For a given model, riftor resolves the key/base in this order: the matching
`[providers.<key>]` entry → the legacy top-level `api_key`/`api_base` → the
provider's environment variable (e.g. `ANTHROPIC_API_KEY`). Environment variables
remain the recommended way to supply keys in shared or CI environments.

## Permissions — `~/.config/riftor/permissions.toml`

Trust choices persist here. `allow` rules auto-approve a tool (optionally only
when the call matches a regex `pattern`); `deny` rules hard-block it without ever
prompting. Destructive `bash` patterns (`rm -rf`, `dd of=/dev/…`, `mkfs`, fork
bombs) are denied by default.

```toml
[permissions]
allow = [
  { tool = "read" },
  { tool = "bash", pattern = '^nmap\b' },   # auto-approve nmap, prompt for the rest
]
deny = [
  { tool = "bash", pattern = 'shutdown|reboot' },
]
```

Manage rules live with `/permissions allow <tool> [pattern]` and
`/permissions deny <tool> [pattern]`, or pick **Always (w)** / **Never (d)** in
an approval prompt. In headless mode (`--prompt`), approval-gated tools only run
if a standing `allow` rule exists.

## Keybindings — `~/.config/riftor/keybindings.toml`

Override the built-in hotkeys (`action = key`):

```toml
[keybindings]
clear = "ctrl+k"      # rebind the clear action
quit  = "ctrl+q"
```

## Troubleshooting

- **`authentication failed`** — the key for your model's provider is missing or
  wrong. Check the right env var (e.g. `ANTHROPIC_API_KEY`), or set the key in
  `/config` (stored under `[providers.<key>]`).
- **`context ~NN% of window`** — the conversation is large. Run `/compact` to
  shrink old tool output, or `/clear` to start fresh.
- **`model '…' has no known provider prefix`** — the id is likely a typo; use a
  `provider/name` form litellm recognises.
- **A scan is blocked as out-of-scope** — add the target with `/scope add`, or
  preview without blocking via `/scope dry`.
