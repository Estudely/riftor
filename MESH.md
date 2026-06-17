# Riftor Mesh — Master Tracking

> Feature branch: `feature/mesh-phase1`  
> Last updated: 2026-06-16  
> Status: **Cross-machine verified — Mac submitted finding over iroh P2P to Linux Commander**

## Where to pick up

```bash
git clone https://github.com/Estudely/riftor.git
cd riftor
git checkout feature/mesh-phase1

# Build daemon
cargo build --manifest-path meshd/Cargo.toml --release

# Install Python deps
uv sync --extra dev

# Run TUI
uv run riftor
```

## Quick resume test

**Linux (Commander):** `uv run riftor` → `/mesh-create test` → `/mesh test`
**Mac (Worker):** copy-paste the command from `/mesh test` output

The full P2P pipeline: Worker → iroh QUIC → Commander Router → AI Processor → Published.
All cross-machine verified 2026-06-16.

---

## What We Built

### Architecture

```
┌─ riftor (Python TUI) ─────────────────────────┐
│  MeshSidebar  │  /mesh commands  │  MeshManager │
│  MeshClient → JSON-line protocol → stdin/stdout │
└──────────────────────┬─────────────────────────┘
                       │
┌─ riftor-meshd (Rust daemon) ───────────────────┐
│  Handler ──► Processor ──► LLM Client (DeepSeek)│
│     │              │                             │
│  docs (iroh-docs)  gossip (iroh-gossip)  blobs (iroh-blobs) │
│     │              │                             │
│  Router (ALPN: riftor-mesh/0 + iroh-docs + iroh-gossip + iroh-blobs) ◄── P2P QUIC ──► │
└─────────────────────────────────────────────────┘
```

### Files Created/Modified

| Path | Purpose |
|---|---|
| `meshd/Cargo.toml` | Rust deps: iroh v1, iroh-docs, iroh-gossip, iroh-blobs, reqwest, etc. |
| `meshd/src/main.rs` | Entry: creates Endpoint + Router, stdin loop, P2P keep-alive |
| `meshd/src/lib.rs` | Module declarations |
| `meshd/src/protocol.rs` | JSON-line types: Request, Response, Event |
| `meshd/src/handler.rs` | 15 RPC handlers: CRUD, submit, processor control, P2P dial |
| `meshd/src/identity.rs` | Real iroh SecretKey, persisted to disk |
| `meshd/src/engagement.rs` | Engagement CRUD, invite encode/decode, state queries |
| `meshd/src/mesh_stack.rs` | Builds the shared iroh stack (blobs+gossip+docs) on the router endpoint, persistent with in-memory fallback |
| `meshd/src/docs.rs` | iroh-docs CRDT replica per engagement (persisted; Commander writes, Workers get read-only `DocTicket` replicas) |
| `meshd/src/gossip.rs` | iroh-gossip topic pub/sub (`join`/`broadcast`/`subscribe_stream` over real gossip) |
| `meshd/src/blobs.rs` | In-memory blob store |
| `meshd/src/queue.rs` | Bounded mpsc submission queue with stats |
| `meshd/src/llm.rs` | LLM client: DeepSeek HTTP, 3-retry, circuit breaker |
| `meshd/src/prompts.rs` | Dedup + severity assessment prompt templates |
| `meshd/src/processor.rs` | Worker pool, pipeline, review queue, 3 modes |
| `meshd/src/p2p.rs` | P2P protocol handler, Router, dial/stream |
| `meshd/tests/integration_test.rs` | Full daemon flow via stdio |
| `meshd/tests/processor_test.rs` | Queue, modes, review operations |
| `meshd/tests/p2p_test.rs` | Two endpoints echo over iroh QUIC |
| `riftor/mesh/__init__.py` | Public API exports |
| `riftor/mesh/protocol.py` | Async JSON-line client (MeshProtocol) |
| `riftor/mesh/daemon.py` | Subprocess manager for riftor-meshd |
| `riftor/mesh/client.py` | High-level API: engagement, submit, processor RPCs |
| `riftor/mesh/manager.py` | Orchestrator: daemon + client + events |
| `riftor/mesh/models.py` | Pydantic models: Finding, Host, Service, Task |
| `riftor/mesh/events.py` | Event dispatch: state_changed, member_joined, etc. |
| `riftor/mesh/sidebar.py` | Textual widget: mesh sidebar |
| `riftor/mesh/commands.py` | Slash commands: /mesh join, mode, review, etc. |
| `riftor/tui/app.py` | TUI integration: sidebar, commands, handlers |
| `riftor/tui/themes/rift.tcss` | CSS for mesh sidebar |
| `tests/mesh/test_protocol.py` | 4 tests |
| `tests/mesh/test_models.py` | 4 tests |
| `tests/mesh/test_daemon.py` | 3 tests |
| `tests/mesh/test_client.py` | 4 tests |
| `tests/mesh/test_manager.py` | 4 tests |
| `dev/mesh_e2e.py` | End-to-end daemon flow test |
| `dev/mesh_live_test.py` | Live LLM pipeline test (needs API key) |
| `dev/mesh_p2p_test.py` | Cross-process P2P test |
| `dev/mesh_xmachine_test.py` | Cross-machine P2P test |

---

## Test Suite

| Layer | Tests | Status |
|---|---|---|
| Rust unit (protocol, queue, llm, prompts) | 14 | ✅ |
| Rust integration (daemon flow) | 1 | ✅ |
| Rust processor (queue, modes, review) | 5 | ✅ |
| Rust P2P (echo between endpoints) | 1 | ✅ |
| Python mesh (protocol, models, daemon, client, manager) | 19 | ✅ |
| Existing riftor tests | 396 | ✅ (no regressions) |
| **Total** | **436** | ✅ |

**Lint:** 0 errors | **Type check:** 0 errors, 0 warnings  
**Platforms verified:** Linux x86_64, macOS ARM64 (M1)

---

## Features Delivered

### Phase 1: P2P Infrastructure
- [x] Rust sidecar daemon (`riftor-meshd`) with JSON-line protocol
- [x] Engagement CRUD: create, join via invite, submit findings
- [x] Real iroh v1 Endpoint with Ed25519 identity
- [x] P2P Router on ALPN `riftor-mesh/0`
- [x] `get_node_addr` RPC returns NodeId + addresses
- [x] `p2p_dial` RPC connects to remote peer
- [x] Cross-machine P2P verified (Linux ↔ Mac M1 QUIC echo)
- [x] Python mesh module with Textual TUI sidebar
- [x] `/mesh` slash commands (join, leave, invite, status, etc.)

### Phase 2: AI Findings Processor
- [x] Bounded submission queue (mpsc channel, 256 capacity)
- [x] Worker pool (3 concurrent tokio tasks)
- [x] LLM client with retry + circuit breaker (DeepSeek/OpenAI)
- [x] Dedup prompt: compares new finding against existing docs
- [x] Severity prompt: CVSS v3.1 assessment with engagement context
- [x] 3 processing modes: autonomous, review-required, critical-only
- [x] Review queue: Commander approves/rejects/overrides decisions
- [x] Live verified with DeepSeek API (dedup, severity, publish)
- [x] Queue stats, circuit breaker status, mode toggle
- [x] `/mesh review`, `/mesh approve`, `/mesh reject`, `/mesh mode`, `/mesh queue`

---

## Riftor Mesh Commands

| Command | Action |
|---|---|
| `/mesh join <invite>` | Join an engagement swarm |
| `/mesh leave` | Leave current engagement |
| `/mesh invite` | Generate invite string |
| `/mesh status` | Show engagement stats |
| `/mesh members` | List members + online status |
| `/mesh mode autonomous\|review\|critical` | Set processor mode |
| `/mesh queue` | Show queue stats |
| `/mesh processor` | Processor status + circuit breaker |
| `/mesh review` | List pending review decisions |
| `/mesh approve <id>` | Approve a pending decision |
| `/mesh reject <id> <reason>` | Reject with reason |
| `/mesh refresh` | Sync state from docs |

---

## Remaining Work

### Small (1-2 hours each)
- [ ] **Persist endpoint identity** — each daemon restart generates new NodeId. Use identity.rs persisted key for the endpoint
- [ ] **Fix unused variable warnings** — `queue` in handler, `warn`/`P2pStream` in main

### Medium (2-4 hours each)
- [x] **Route P2P submissions to processor** — ✅ P2P handler now enqueues into Commander's queue
- [x] **Real findings sync over P2P** — ✅ Worker submits over P2P → Commander processes → docs updated
- [x] **Swap docs stub → real iroh-docs** — ✅ CRDT-synced state, persisted to disk, read-only `DocTicket` replicas; namespace per engagement
- [x] **Swap gossip stub → real iroh-gossip** — ✅ topic pub/sub wired to Router; receive loops bridge gossip → JSON-line `MeshEvent` → Python TUI live updates
- [ ] **Merge to main + release**

### Large (Phase 2 extras)
- [ ] Task board Kanban (Backlog → In Progress → Done)
- [ ] Live terminal watch (read-only terminal sharing)
- [ ] Shared scratchpad (collaborative markdown)
- [ ] Evidence chain (blob links in findings)

### Phase 3 (Hive Scale)
- [ ] Portable cryptographic reputation
- [ ] Reputation-gated swarms
- [ ] Public swarm directories
- [ ] Contribution proofs for bounty splits

---

## Quick Reference

```bash
# Build
cargo build --manifest-path meshd/Cargo.toml --release

# Run daemon normally (exits after stdin closes)
echo '{"id":1,"method":"ping"}' | ./meshd/target/release/riftor-meshd

# Run daemon with P2P (stays alive after stdin)
RIFTOR_MESH_P2P=1 ./meshd/target/release/riftor-meshd

# Run all tests
cargo test --manifest-path meshd/Cargo.toml
uv run pytest tests/mesh/ -v
uv run pytest tests/ --ignore=tests/mesh

# Live LLM test
export DEEPSEEK_API_KEY="sk-..."
uv run python dev/mesh_live_test.py

# Cross-machine P2P
# Terminal 1 (Linux): RIFTOR_MESH_P2P=1 ./meshd/target/release/riftor-meshd
# Terminal 2 (Linux): echo '{"id":1,"method":"get_node_addr"}' | ./meshd/target/release/riftor-meshd
# Terminal 3 (Mac):   RIFTOR_MESH_P2P=1 ./meshd/target/release/riftor-meshd <<< '{"id":1,"method":"p2p_dial","params":{"node_id":"<NODE_ID>","addresses":[...]}}'
```
