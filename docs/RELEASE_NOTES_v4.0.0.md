# riftor v4.0.0 — methodology checklist & workers

## Highlights
- **OWASP/PTES methodology checklist** — replaces RIFT stage tracking; auto-ticks
  on relevant tool runs; `/methodology` and `list_methodology` / `check_methodology`
- **Workers** — `dispatch_worker` fans out parallel subagents; built-in roles
  (recon, scout, tester, analyst, exploiter) + custom specs in
  `~/.config/riftor/workers/`
- **Cleaner config** — `worker_model`, `worker_max_parallel`, `worker_timeout_s`;
  lore/genz/Baaj-Chakla labels removed
- **Professional positioning** — authorized bug bounty / pentest agent; no lore
  persona overlay

## Breaking changes

### Removed
- **RIFT stage model** — no `/stage`, no `set_stage` tool, no `[R·I·F·T]` status bar
- **Persona toggles** — `lore`, `genz`, `/lore`, `/genz` removed
- **Baaj/Chakla naming** — `label_main`, `label_worker` removed; UI uses Lead/Worker
- **`dispatch_chakla` tool** — renamed to `dispatch_worker`

### Renamed config keys
| v3 | v4 |
|---|---|
| `chakla_model` | `worker_model` |
| `chakla_max_workers` | `worker_max_parallel` |
| `chakla_timeout_s` | `worker_timeout_s` |

### Renamed CLI
| v3 | v4 |
|---|---|
| `--chakla-model` | `--worker-model` |

`--chakla-model` is still accepted as a hidden alias for one release cycle.

## Migration guide

### 1. Update config (optional but recommended)
riftor **reads** v3 keys and maps them in memory on startup, but does not rewrite
your file. Edit `~/.config/riftor/config.toml` when convenient:

```toml
[riftor]
# was: chakla_model = "anthropic/claude-haiku-4-5-20251001"
worker_model = "anthropic/claude-haiku-4-5-20251001"
worker_max_parallel = 5      # was: chakla_max_workers
worker_timeout_s = 300       # was: chakla_timeout_s
```

Remove obsolete keys: `lore`, `genz`, `label_main`, `label_worker`,
`chakla_model`, `chakla_max_workers`, `chakla_timeout_s`.

### 2. Update scripts and CI
- Replace `dispatch_chakla` references with `dispatch_worker` in any custom
  prompts or automation (the agent learns the new name from the system prompt).
- Replace `--chakla-model` with `--worker-model` in shell scripts.
- Permission rules: if you granted `dispatch_chakla`, add `dispatch_worker`.

### 3. Replace stage workflows
Instead of `/stage R` or `set_stage`, use `/methodology` to see checklist
progress. The agent ticks items via `check_methodology` and auto-tick on tool
runs (e.g. nmap → Port scanning).

### 4. Custom workers (optional)
Add role definitions under `~/.config/riftor/workers/<name>.md`:

```markdown
---
name: my-scanner
description: Custom passive recon worker
model: ""
tools: [bash, webfetch]
---
You are a recon worker. Run only the checks in the task string...
```

User specs override bundled roles on name collision. List all roles with `/workers`.

## Install
```bash
pip install -U riftor
# optional extras:
pip install -U 'riftor[browser]'
pip install -U 'riftor[mcp]'
```

## Links
- Site: https://riftor.dev
- Docs: https://github.com/Estudely/riftor/blob/main/docs/configuration.md
- Repo: https://github.com/Estudely/riftor
- Prior: [v3.6.0](https://github.com/Estudely/riftor/releases/tag/v3.6.0)
