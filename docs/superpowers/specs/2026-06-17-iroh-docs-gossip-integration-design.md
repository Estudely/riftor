# Real iroh-docs + iroh-gossip Integration — Design

**Date:** 2026-06-17
**Branch:** `feature/mesh-phase1`
**Status:** Approved design — pending implementation plan

## Goal

Replace the two in-memory stubs in the Rust mesh daemon (`meshd/src/docs.rs`,
`meshd/src/gossip.rs`) with real iroh primitives, delivering two user-facing
capabilities:

- **(A) Real-time multi-operator sync** — when one operator records a finding,
  other peers see it appear live without manually running `/mesh-refresh`.
- **(B) Resilience / no single point of failure** — engagement state survives a
  Commander restart and is fully replicated to every peer, so any peer can serve
  state from its own local replica even when the Commander is offline.

## Context & current state

- **`docs.rs`** — in-memory `HashMap`. It is the source of truth for
  findings/hosts/services. Consumed by `processor.rs` (`query_similar`,
  `get_all`, `insert`), `engagement.rs` (`open`, `get_all`), and the p2p
  `get_state` handler in `p2p.rs`.
- **`gossip.rs`** — in-memory `HashMap`. **`listen()` is never consumed by any
  code** — gossip is currently write-only dead weight. `join`/`broadcast` are
  called from `engagement.rs` but nothing reacts to the messages.
- **Sync today** is strict hub-and-spoke pull: a Worker dials the Commander and
  calls p2p `submit` (push into the Commander's queue → processor → docs) and
  `get_state` (read the Commander's docs). No CRDT, no live push.
- **Compatibility (verified):** `iroh-docs 0.101.0` and `iroh-gossip 0.101.0`
  both depend on `iroh = "1"`; the lockfile resolves a single `iroh 1.0.0`.
  `iroh-blobs 0.103.0` is already a dependency. Integration is feasible without
  a version conflict. (Independent crate version numbers; not an iroh-core
  mismatch.)

## Decisions (from brainstorming)

1. **Goals:** both A (live sync) and B (resilience). Both crates earn their place.
2. **Topology — Model B (replicated, Commander-authoritative writes):** Workers
   hold full live CRDT *read*-replicas; writes to canonical findings still flow
   through the Commander's AI pipeline (validate → dedup → severity → publish).
3. **Persistence:** persist to disk via the `fs-store` feature, consistent with
   the existing persisted `identity.key`. A lone Commander's state survives
   restart.
4. **Capability:** Workers receive **read-only** `DocTicket`s. Commander-only
   writes become a cryptographic guarantee, not a convention.
5. **Phasing:** one combined spec, **docs-first** sequencing. Land iroh-docs
   (foundation: replicated state, tickets, persistence) and verify end-to-end,
   then layer iroh-gossip (live event topics + TUI consumers).

## Architecture

The swap **preserves the public method signatures** of `DocsStore` and
`GossipStore` (the seam the consumers already speak to) and replaces only their
internals with real iroh primitives. `Handler`, `EngagementManager`,
`Processor`, and the p2p handler change minimally.

### Shared foundation (built once, used by both)

At daemon startup in `main.rs`, on the **router endpoint** (the
persisted-identity endpoint), build the iroh stack:

- `iroh-blobs` persistent store at `~/.local/share/riftor-mesh/blobs`
- `iroh-docs` via `Docs::persistent(.../docs)`, spawned as a protocol on the
  Router under its ALPN (`iroh_docs::ALPN`)
- `iroh-gossip` via `Gossip::builder().spawn(endpoint)`, spawned as a protocol on
  the Router under its ALPN (`iroh_gossip::ALPN`)

All three ALPNs register on the **same Router** alongside the existing
`riftor-mesh/0` handler. The `Docs` and `Gossip` handles are threaded into
`Handler::new(...)` → `EngagementManager` → `Processor`, replacing the
`Arc<DocsStore>` / `Arc<GossipStore>` construction at `handler.rs:36-37`.

### Component responsibilities (post-change)

- **`DocsStore`** — thin wrapper over `DocsApi` + one `Doc` (CRDT replica) per
  engagement. Owns the `engagement_id → (Doc, NamespaceId)` map, key encoding,
  and JSON (de)serialization. Unit-testable via `Docs::memory()`.
- **`GossipStore`** — wrapper over `Gossip`; owns `topic_id → GossipTopic`
  sender/receiver handles plus a spawned receive loop per topic that forwards
  events to consumers. Testable with two in-process gossip nodes.
- **`Handler` / `EngagementManager` / `Processor`** — logic unchanged; receive
  real handles instead of stub handles.

## iroh-docs design

### Namespace = engagement

Each engagement maps 1:1 to one iroh-docs replica (a `NamespaceId`). The existing
`engagement_id` remains the app identifier; a persisted mapping
`engagement_id → NamespaceId` lets the daemon reopen the right replica after
restart.

### Persistence

Enable the `fs-store` feature on `iroh-docs` / `iroh-blobs`. Blobs + docs live
under `~/.local/share/riftor-mesh/` (same data-dir as `identity.key`). On
startup `Docs::persistent(path)` reopens existing replicas.
`DocsStore::open(engagement_id)` resolves-or-creates the namespace (Commander,
first time) or is a no-op if it already exists locally.

New on-disk artifacts:
- `~/.local/share/riftor-mesh/blobs/`
- `~/.local/share/riftor-mesh/docs/`
- `~/.local/share/riftor-mesh/namespaces.json` — `engagement_id → NamespaceId`
  plus the Commander's persisted `AuthorId`.

No migration of existing data: the old store was in-memory, so nothing is
persisted today.

### Key / value encoding

iroh-docs entries are `(author, key) → bytes`. We encode:

- **key** = `"{doc_type}/{key}"` (e.g. `finding/<uuid>`), so `get_all(doc_type)`
  becomes a prefix query (`Query::key_prefix("{doc_type}/")`).
- **value** = the JSON `Value` serialized to bytes, stored inline via
  `set_bytes`.
- A single daemon-level **`AuthorId`** (persisted alongside identity) signs all
  Commander writes.

### Method mapping (signatures unchanged)

| `DocsStore` method | iroh-docs implementation |
|---|---|
| `open(eid)` | resolve-or-create namespace; record `eid→NamespaceId` |
| `insert(eid, type, key, val)` | `doc.set_bytes(author, "{type}/{key}", json)` |
| `get_all(eid, type)` | `doc.get_many(Query::key_prefix("{type}/"))` → deserialize each |
| `query_similar(eid, target, class, n)` | `get_all("finding")` then filter in memory (logic identical to today) |

### Capability / invites (read-only Workers)

When the Commander creates an engagement, it generates the namespace and a
**read ticket** via `doc.share(ShareMode::Read, AddrInfoOptions::…)` →
`DocTicket`. The ticket string is embedded in the existing invite alongside the
current NodeId/addresses. The Worker calls `docs.import(ticket)` and receives a
**read-only replica** that syncs from the Commander. Workers cannot write to the
canonical doc — enforced by the iroh-docs capability check at the protocol
layer.

### Write path under Model B

1. Worker records a finding → existing p2p `submit` to the Commander's queue
   (unchanged).
2. Commander's processor validates / dedups / severity-scores → calls
   `docs.insert(...)` → `doc.set_bytes`.
3. iroh-docs **CRDT-syncs** the new entry to every joined Worker's read-replica.
4. `get_state` on any node reads its **local replica** (`get_all`), so even a
   Worker can serve state from its own replica → resilience goal B.

`get_state` becomes **local-first**: each node reads its own synced replica, so a
Worker disconnected from the Commander still answers `get_state` from local data.

**Consistency note:** iroh-docs sync is eventually-consistent and propagates
while peers are connected; an offline Worker catches up via delta sync on
reconnect. This is expected CRDT behavior and aligns with goals A + B.

## iroh-gossip design

Delivers goal A (live updates) and **adds the consumers that do not exist
today**.

### Topics

Keep the naming `riftor/{engagement_id}/{subtopic}`, hashed (blake3) into a
gossip `TopicId` (32 bytes). Each of the four existing subtopics now has a
defined purpose and a consumer:

| Subtopic | Producer | Consumer / effect |
|---|---|---|
| `activity` | any node, on notable actions | TUI sidebar live activity feed |
| `presence` | each node, periodic heartbeat | TUI sidebar member list (online/offline) |
| `processed` | **Commander**, after the processor publishes a finding | Workers get a nudge → trigger a docs re-read / refresh so the UI updates the instant a finding is processed (low-latency complement to CRDT sync) |
| `submit` | Worker, on local submission | Commander nudge (optional parity; direct p2p `submit` already covers this — lowest priority) |

### Method mapping (signatures preserved, plus one addition)

| `GossipStore` method | iroh-gossip implementation |
|---|---|
| `join(eid, subtopic)` | `gossip.subscribe(topic_id, bootstrap_peers)` → store the `GossipTopic`, split into sender + receiver |
| `broadcast(eid, subtopic, msg)` | `sender.broadcast(json_bytes)` |
| **new:** `subscribe_stream(eid, subtopic)` | return the receiver so a consumer task can `await` incoming `Event::Received` messages |

### Bootstrap peers

Gossip requires initial peers to join a topic's swarm. The Worker bootstraps from
the **Commander's NodeId** (already in the invite). The Commander starts as the
sole member; Workers join via it; the Plumtree / HyParView overlay
self-organizes.

### Receive loop (the missing consumer)

For each joined topic, `GossipStore` spawns a task that reads the
`GossipReceiver` stream and forwards decoded messages to a **daemon → Python
event channel**. Decoded gossip events become **JSON-line `Event` notifications**
emitted on the daemon's stdout (the protocol already has an `Event` type in
`protocol.rs`). The Python `events.py` dispatch system routes them to
`sidebar.py` to update the live activity feed, member presence, and finding
count.

### End-to-end live-update path

> Commander processes finding → writes to docs (CRDT syncs replica) **and**
> broadcasts a `processed` gossip event → Worker daemon's receive loop emits an
> `Event` on stdout → Python `events.py` → `sidebar.py` re-renders. The operator
> sees the finding appear live without `/mesh-refresh`.

### Scope guard (YAGNI)

Keep the four existing subtopics only. No new message types beyond what the
sidebar needs (activity line, presence heartbeat, processed-finding nudge).
Presence is a simple periodic heartbeat, not a full membership protocol — the
iroh-gossip overlay handles the hard part.

## Error handling & failure modes

### Startup / construction

Building the blobs + docs + gossip stack is fallible (disk I/O, store open). Per
the project's **"bad config never crashes"** convention:

- Persistent store fails to open (corrupt/locked) → log and fall back to an
  **in-memory** docs/blobs store for that session; the daemon still starts and
  loses only cross-restart persistence until fixed.
- Gossip spawn failure → log and continue without live updates; docs CRDT sync
  still works, the TUI just won't get push nudges.

### Per-operation errors (method seam keeps `anyhow::Result`)

- `insert` / `set_bytes` failure → propagate `Err` to the processor, which
  already handles publish failures (logs, doesn't crash the worker pool).
- `get_all` on an unknown / unopened engagement → `Ok(vec![])` (matches today's
  stub behavior; no panic).
- `import(ticket)` failure on a Worker (bad/expired ticket, Commander
  unreachable) → typed error surfaced as a `/mesh-join` error in the TUI (same
  path as today's dial errors).

### Replication failure modes (inherent to CRDT — documented, not "fixed")

- Commander offline → Workers serve `get_state` from their **local replica**
  (goal B); new submissions queue/retry until reconnect.
- Worker offline → catches up via delta sync on reconnect; no data loss.
- Conflicting writes → impossible for findings: Workers are read-only
  (capability-enforced), so there is a **single writer per namespace** → no merge
  conflicts on findings (clean side-benefit of Model B + read tickets).

### Capability enforcement

A Worker attempting to write is rejected by iroh-docs at the protocol layer. No
app-level check is required, but a debug assertion/log fires if a Worker ever
calls `insert`, so misuse is visible during development.

### Backward-compat for invites

The invite format gains a `doc_ticket` field. Decoding is **tolerant**: an invite
without `doc_ticket` (old format) still joins for p2p submit/get_state but won't
get a CRDT replica — logged as a downgrade. In-flight invites do not hard-fail.

## Testing strategy

Existing discipline holds: offline by default; both Rust and Python suites stay
green. iroh-docs/gossip run fully in-process with in-memory stores and no
network, so tests remain offline.

### Rust unit tests (stores in isolation)

- `DocsStore` over `Docs::memory()`: `open` → `insert` three doc_types →
  `get_all` returns them per-type; prefix isolation (`finding/x` does not leak
  into `get_all("host")`); `query_similar` filters by target/class; `get_all` on
  unknown engagement → empty.
- Round-trip serialization: insert a JSON `Value`, read it back byte-identical.
- `GossipStore`: two in-process gossip nodes on a local endpoint join the same
  topic; node A `broadcast` → node B's `subscribe_stream` receives the decoded
  message. Topic-id derivation is deterministic for the same
  `engagement_id/subtopic`.

### Rust integration tests (extend existing `tests/`)

- **Read-ticket replication (keystone for A + B):** two in-process daemons;
  Commander creates engagement + namespace, shares a **read ticket**; Worker
  `import`s it; Commander `insert`s a finding; assert it appears in the Worker's
  replica via `get_all`; assert the Worker **cannot** write (capability error).
- **Persistence:** open `Docs::persistent(tmpdir)`, insert, drop, reopen from the
  same dir, assert data survives.
- Extend `tests/p2p_test.rs` / `integration_test.rs` so the new ALPNs coexist on
  one Router with `riftor-mesh/0`.

### Python tests (`tests/mesh/`)

- `events.py` dispatch: feed a synthetic gossip-derived `Event` JSON-line; assert
  it routes to the sidebar handler and updates state (mock daemon, no network —
  matches existing `test_client.py` mock style).
- Keep all 19 existing mesh tests green; update any that assumed in-memory stub
  semantics.

### End-to-end (manual, dev scripts)

- Extend `dev/mesh_p2p_test.py` (two daemons, same machine) to assert: Commander
  processes a finding → Worker's `/mesh findings` shows it **without**
  `/mesh-refresh` (live path); Worker `get_state` works while the Commander is
  killed (resilience path).

### CI gates (unchanged)

`cargo test`, `cargo clippy` (zero warnings), `uv run pytest`, smoke — all must
pass. New tests run offline.

## Implementation sequencing (docs-first)

- **Phase 1 — iroh-docs:** add `fs-store`, build blobs+docs on the router
  endpoint, namespace-per-engagement, key encoding, persistence,
  `namespaces.json`, read-ticket generation in invites, Worker `import`,
  local-first `get_state`. Plus Phase 1 tests. Ends green; goal B verified.
- **Phase 2 — iroh-gossip:** spawn gossip on the router endpoint, real
  `join`/`broadcast`, new `subscribe_stream` + per-topic receive loop, daemon →
  Python `Event` bridge, `events.py`/`sidebar.py` consumers, presence heartbeat.
  Plus Phase 2 tests. Ends green; goal A verified.

Each phase is independently shippable.

## Key API references (iroh-docs 0.101 / iroh-gossip 0.101)

- `iroh_docs::protocol::Docs::persistent(path) -> Builder`; `::memory()` for tests
- `DocsApi::create() -> Doc`; `DocsApi::import(DocTicket) -> Doc`;
  `import_and_subscribe`
- `Doc::set_bytes(author, key, bytes)`; `Doc::get_many(Query)`;
  `Doc::get_one(Query)`
- `Doc::share(ShareMode::Read | ShareMode::Write, AddrInfoOptions) -> DocTicket`
- `iroh_docs::{NamespaceId, AuthorId, Capability, DocTicket, ALPN}`
- `iroh_gossip::net::Gossip::builder().spawn(endpoint)`;
  `Gossip::subscribe(topic_id, bootstrap)`; `GossipSender::broadcast(bytes)`;
  `GossipReceiver` stream of `Event::Received`
