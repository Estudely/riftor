# riftor v3.6.0 — session & rollback QoL

Find the rift. Open it. Cross through.

## Highlights
- **Session prune + prefix IDs** — `/sessions rm <id|old [days]>`; `/resume`
  accepts a unique prefix or suffix
- **`/rollback` UX** — numbered `[1]` user turns on replay; `/rollback last [k]`;
  message-index off-by-one vs `dump()` fixed
- **Context overflow → compact offer** — when the provider hits the window limit,
  a modal offers `/compact` then nudges `/retry`
- **`make demo-headless`** — offline one-liner via `RIFTOR_DEMO_RESPONSE` (skips
  the API-key gate when demo env is set)
- **Headless permissions cookbook** — minimal CI allowlist snippet in
  `docs/configuration.md`

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
- Prior: [v3.5.0](https://github.com/Estudely/riftor/releases/tag/v3.5.0) (slash registries, headless exit 4)
