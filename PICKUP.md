# PICKUP.md — Riftor Mesh Resume Guide

Clone, build, run:

```bash
git clone https://github.com/Estudely/riftor && cd riftor
git checkout feature/mesh-phase1
cargo build --manifest-path meshd/Cargo.toml --release
uv sync --extra dev
uv run riftor
```

## File Map

### Rust daemon (`meshd/` — the P2P engine)

| File | What it does |
|---|---|
| `src/main.rs` | Entry: creates iroh Endpoint + Router, stdin JSON-line loop, P2P keep-alive |
| `src/handler.rs` | 15 RPC handlers: engagement CRUD, submit, processor control, p2p_dial, p2p_submit_remote |
| `src/p2p.rs` | P2P protocol handler (ALPN `riftor-mesh/0`), Router, dial/stream, routes `submit`/`ping`/`get_state` |
| `src/processor.rs` | Worker pool, AI pipeline (validate→dedup→severity→publish), review queue, 3 modes |
| `src/llm.rs` | HTTP client for DeepSeek/OpenAI, 3-retry, circuit breaker |
| `src/identity.rs` | Real iroh SecretKey, persisted to disk |
| `src/queue.rs` | Bounded mpsc submission queue with stats |
| `src/prompts.rs` | Dedup + severity LLM prompt templates |
| `src/engagement.rs` | Engagement CRUD, invite encode/decode, state queries |
| `src/docs.rs` | iroh-docs CRDT replica per engagement (persisted, read-ticket replicas) |
| `src/gossip.rs` | iroh-gossip topic pub/sub (real `join`/`broadcast`/`subscribe`) |
| `src/protocol.rs` | JSON-line types: Request, Response, Event |
| `tests/p2p_test.rs` | ✅ Two endpoints echo over iroh QUIC (integration test) |
| `tests/processor_test.rs` | ✅ Queue, modes, review operations |
| `tests/integration_test.rs` | ✅ Full daemon flow via stdio |

### Python module (`riftor/mesh/` — the TUI layer)

| File | What it does |
|---|---|
| `manager.py` | Orchestrator: daemon lifecycle, engagement ops, processor control |
| `daemon.py` | Spawns/manages riftor-meshd subprocess (sets `RIFTOR_MESH_P2P=1`) |
| `client.py` | High-level async client: engagement, submit, processor RPCs |
| `protocol.py` | Async JSON-line client for daemon communication |
| `models.py` | Pydantic models: Finding, Host, Service, Task, Engagement |
| `sidebar.py` | Textual widget: mesh sidebar (status, members, activity, processor) |
| `commands.py` | Slash command registration helpers |
| `events.py` | Event dispatch system |

### TUI integration (`riftor/tui/`)

| File | What changed |
|---|---|
| `app.py` | MeshSidebar in compose(), MeshManager in on_mount(), 11 mesh command handlers, `/mesh findings` |
| `themes/rift.tcss` | CSS for mesh sidebar styling |

### Tests (`tests/mesh/`)

| File | Tests |
|---|---|
| `test_protocol.py` | 4 — JSON-line framing, request IDs |
| `test_models.py` | 4 — Pydantic model defaults |
| `test_daemon.py` | 3 — Binary discovery, lifecycle guards |
| `test_client.py` | 4 — Mock-based API calls |
| `test_manager.py` | 4 — Manager lifecycle, state updates |

### Test scripts (`dev/`)

| File | Usage |
|---|---|
| `mesh_e2e.py` | Full daemon flow: ping → create identity → engagement → submit → state |
| `mesh_live_test.py` | Live LLM pipeline test (needs `DEEPSEEK_API_KEY`) |
| `mesh_p2p_test.py` | Cross-process P2P test (2 daemons on same machine) |
| `mesh_mvp_test.py` | Cross-machine P2P test helper |
| `mesh_mvp.sh` | Shell version of MVP test |

## TUI Commands

| Command | Action |
|---|---|
| `/mesh` | Status + available commands |
| `/mesh-create <name>` | Create engagement |
| `/mesh-join <invite>` | Join via P2P invite |
| `/mesh-invite` | Generate invite string |
| `/mesh findings` | List findings from mesh docs |
| `/mesh-refresh` | Sync state |
| `/mesh-leave` | Leave engagement |
| `/mesh test` | Show P2P test command (copy-paste for cross-machine) |
| `/mesh mode autonomous\|review\|critical` | Processor mode |
| `/mesh queue` | Processor queue stats |
| `/mesh processor` | Processor + circuit breaker status |
| `/mesh review` | Pending review decisions |
| `/mesh approve <id>` | Approve pending |
| `/mesh reject <id> <reason>` | Reject pending |

## Quick Tests

```bash
# All tests (Rust + Python)
cargo test --manifest-path meshd/Cargo.toml
uv run pytest tests/mesh/ -v
uv run pytest tests/ --ignore=tests/mesh   # existing riftor tests

# Daemon ping
echo '{"id":1,"method":"ping"}' | ./meshd/target/debug/riftor-meshd

# Live LLM test
export DEEPSEEK_API_KEY="sk-..."
uv run python dev/mesh_live_test.py

# Cross-machine P2P
# Linux: uv run riftor → /mesh-create test → /mesh test → copy command
# Mac:   paste command
# Linux: /mesh findings
```

## Remaining Work

### Small
- [x] Persist endpoint identity across restarts (use identity.rs key for iroh Endpoint)
- [x] Fix unused import warnings

### Medium
- [x] Swap docs stub → real iroh-docs (CRDT synced state, persisted, read-ticket replicas)
- [x] Swap gossip stub → real iroh-gossip (topic pub/sub + live MeshEvent bridge to TUI)
- [ ] Merge to main + release

### Follow-ups discovered during iroh integration
- [ ] `join_engagement` runs `docs.import_ticket` (a P2P dial) on the daemon's
  single-threaded request loop; with no transport it blocks indefinitely.
  Spawn the import or bound it with a timeout so the loop stays responsive.
- [ ] Presence heartbeat task has no cancellation handle, so a node keeps
  heartbeating after `leave`.
- [ ] Dedup on the 2nd+ finding still calls the LLM (needs an API key); the
  first finding in a fresh engagement publishes offline.

### Large (Phase 2 extras)
- [ ] Kanban task board
- [ ] Live terminal watch (QUIC stream sharing)
- [ ] Shared scratchpad

## Key Architecture Decisions

- **Hub-and-spoke**: Commander is source of truth (writes), Workers hold read-only iroh-docs replicas
- **iroh-docs**: one CRDT namespace per engagement, persisted under `~/.local/share/riftor-mesh/`; Workers join via a read-only `DocTicket` in the invite
- **iroh-gossip**: topics `riftor/{engagement}/{submit,activity,presence,processed}`; receive loops forward decoded messages as JSON-line `MeshEvent`s on daemon stdout → Python `events.py` → live sidebar
- **All ALPNs on one Router**: `riftor-mesh/0` + iroh-docs + iroh-gossip + **iroh-blobs** (blobs is required so docs peers download entry content, not just metadata)
- **Rust sidecar**: `riftor-meshd` wraps iroh, speaks JSON-line over stdin/stdout
- **Two endpoints**: Router endpoint (P2P incoming, persisted identity) + Handler endpoint (outbound dials)
- **P2P keep-alive**: Set `RIFTOR_MESH_P2P=1` env var for daemon to stay alive
- **LLM via DeepSeek**: OpenRouter-compatible API, circuit breaker at 5 failures
