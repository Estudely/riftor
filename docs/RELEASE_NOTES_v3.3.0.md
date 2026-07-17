# riftor v3.3.0 — post-launch MVPs

Find the rift. Open it. Cross through.

## Highlights
- **MCP stdio client** (`pip install 'riftor[mcp]'`, `[[mcp_servers]]` in config)
- **Bounty scope import** — `/scope bounty` + HackerOne JSON/API
- **Collaborative merge** — `/merge` + `merge_engagement`
- **Attack graph** — `/graph` Mermaid kill-chain MVP
- **Session branching** — `/branch` + `/rollback` (message-level)
- **Custom-provider route markers** for litellm registry collisions
- Offline demo path no longer depends on litellm `mock_response` (fixes
  litellm 1.92+ tools→fastapi import in CI)
- Agent no longer forces `/continue` during recon (#104)
- Dependency bumps: litellm 1.92, ruff, setup-uv

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
