# riftor v3.5.0 — operator QoL

Find the rift. Open it. Cross through.

## Highlights
- **Slash-command registry sync** — autocomplete, help, and palette cover
  `/copy`, `/show`, `/hypotheses`, `/lesson(s)`, `/clearlog`, and friends;
  CI asserts handler keys stay in `_COMMANDS`
- **Crash resume always hints `/retry`** when restoring an incomplete session
- **Headless step-limit clarity** — hitting `max_steps` prints a stderr note
  and exits with code **4** (documented in config + man page)
- **Config docs / completions** aligned with real defaults (`chakla_model=""`,
  `genz`, `skills_dir`)
- **Smoke coverage** for `/branch`, `/rollback`, `/graph`, hypotheses/lessons,
  and `/copy`

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
- Prior: [v3.3.0](https://github.com/Estudely/riftor/releases/tag/v3.3.0) (post-launch MVPs)
