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
| `show_thinking` | bool | `true` | Show the model's reasoning as a dim block above each answer (and on stderr in `--headless`). |
| `show_tool_output` | bool | `true` | Render tool-result blocks in the chat. When off, the `⛏` call line still shows and hidden output stays reachable via `/show <id>`. |
| `reasoning_effort` | string | `medium` | Thinking budget requested from the model: `none` `low` `medium` `high`. `none` (or `show_thinking = false`) sends no reasoning request. |
| `max_steps` | int | `16` | Tool-call steps per task before pausing (extend live with `/continue`). Also caps each Chakla worker's step budget. |
| `max_result_chars` | int | `30000` | Cap on tool output fed back to the model. |
| `result_preview_lines` | int | `25` | Lines of a tool result shown before `…/show <id>`. |
| `rate_limit_per_min` | int | `0` | Cap model calls per minute (`0` = unlimited). |
| `chakla_model` | string | `anthropic/claude-haiku-4-5-20251001` | The cheap worker model used by dispatched Chakla subagents. |
| `chakla_max_workers` | int | `5` | Max number of Chakla workers per dispatch batch. |
| `chakla_timeout_s` | int | `300` | Per-worker wall-clock timeout in seconds. |
| `label_main` | string | `Baaj` | Display name for the orchestrator agent. |
| `label_worker` | string | `Chakla` | Display name for the worker subagents. |

### Example
```toml
[riftor]
model = "anthropic/claude-sonnet-4-6"
temperature = 0.3
max_tokens = 2048
theme = "rift"
lore = true
show_thinking = true
show_tool_output = true
reasoning_effort = "medium"
max_steps = 16
rate_limit_per_min = 0

# Local Ollama instead:
# model = "ollama_chat/llama3.1"
# api_base = "http://localhost:11434"
```

### Subagents (Baaj / Chakla)

The main agent (Baaj) can dispatch a batch of lightweight Chakla workers via the
`dispatch_chakla` tool to run independent tasks (e.g. parallel recon) on a cheaper
model. When the agent proposes a dispatch, the TUI shows an approval prompt that
lists the tasks and grants the workers the tools they need (default: `bash`).

Key properties of the worker fleet:

- **Scope and deny rules always bind workers.** A worker cannot call a
  scope-sensitive tool on an out-of-scope target, and all deny rules from
  `permissions.toml` apply identically.
- **Findings land in the shared engagement database.** Worker tool calls to
  `record_service`, `record_finding`, etc. write to the same `.riftor/engagement.db`
  as the main agent.
- **Concurrency is bounded.** `chakla_max_workers` caps parallel workers;
  each worker's step budget is `max_steps` (shared with the main agent);
  `chakla_timeout_s` sets the wall-clock ceiling. Tune these to stay within rate limits.
- **Worker model defaults to Haiku.** `chakla_model` defaults to
  `anthropic/claude-haiku-4-5-20251001` — a cheap, fast model well-suited to
  bounded recon tasks. Override it with `--chakla-model` at the CLI or by editing
  the config file.

### Live worker visibility

While a `dispatch_chakla` batch runs, the TUI shows a live "flock" table — one
row per worker (queued → running → done/timeout/error) with the worker's current
activity and token count. The table is removed when the dispatch finishes; the
aggregated text summary remains. Worker token/cost accrues in the status-bar 🐦
segment as each worker completes. In headless mode, one progress line per finished
worker is printed to stderr (the agent's answer stays on stdout).

### CLI flags

The following flags are runtime-only overrides (they apply for the current
invocation and are not persisted to config.toml):

| Flag | Mirrors field | Notes |
|---|---|---|
| `--model MODEL` | `model` | Override the main-agent model. |
| `--chakla-model MODEL` | `chakla_model` | Override the Chakla worker model. |
| `--api-key KEY` | `api_key` | Override the API key. |

## Providers & models

Open `/config` to pick a provider and model. The **Provider** dropdown lists
Anthropic, OpenAI, OpenRouter, Gemini, Groq, DeepSeek, Mistral, Ollama, Codex
(ChatGPT), and Custom. Picking one prefills the **Base URL** with that
provider's default and
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

## Codex / ChatGPT subscription login

riftor can run inference through a **ChatGPT Plus, Pro, or Team subscription**
instead of an API key by reusing the credentials from OpenAI's official
[Codex CLI](https://github.com/openai/codex).

### Prerequisites

Install the Codex CLI and authenticate once:

```sh
npm install -g @openai/codex   # or follow the Codex CLI README
codex login                    # opens a browser OAuth flow
```

This writes `~/.codex/auth.json` (or `$CODEX_HOME/auth.json` if that
environment variable is set). riftor reads that file to obtain a token; it
never writes it except to persist a refreshed token.

### Selecting Codex in riftor

**Via `/config`:** open `/config`, set **Provider** to **Codex (ChatGPT)**,
and pick a model such as `codex/gpt-5.5-codex`. No API key is required — a
**Codex login** status line in the panel shows whether you are authenticated
and roughly when the token expires.

**Via CLI flag:**

```sh
riftor --model codex/gpt-5.5-codex
```

Model ids use the `codex/<name>` prefix in all contexts.

### Checking status

`riftor --doctor` reports whether `~/.codex/auth.json` is present and the
session is active. Re-run `codex login` whenever the token has expired or a
call returns an authentication error.

### Billing

Usage is billed to your **ChatGPT subscription**, not to OpenAI API credits.

### Caveat

This feature relies on an **undocumented ChatGPT backend endpoint** that
OpenAI may change without notice. riftor self-heals the required system prompt
when online and falls back to a bundled copy offline. If calls start failing
with auth errors, run `codex login` again to refresh the session.

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

The agent can **request** adding in-scope targets itself via the `add_scope` tool
(e.g. a subdomain it discovered on an in-scope host). Like other privileged tools
it is **approval-gated**: you confirm it in the prompt, and in headless mode it is
blocked unless you add an `allow` rule for `add_scope`. The agent can only *widen*
scope this way — removing, excluding, and clearing remain operator-only via `/scope`.

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
  `/config` (stored under `[providers.<key>]`). For Codex / ChatGPT models, run
  `codex login` to refresh the session token.
- **`context ~NN% of window`** — the conversation is large. Run `/compact` to
  shrink old tool output, or `/clear` to start fresh.
- **`model '…' has no known provider prefix`** — the id is likely a typo; use a
  `provider/name` form litellm recognises.
- **A scan is blocked as out-of-scope** — add the target with `/scope add`, or
  preview without blocking via `/scope dry`.
