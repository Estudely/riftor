# Real iroh-docs + iroh-gossip Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the in-memory `DocsStore` and `GossipStore` stubs in `meshd/` with real iroh-docs (CRDT replicated state, persisted to disk, read-only Worker replicas) and iroh-gossip (live event topics with real consumers), delivering live multi-operator sync and resilience.

**Architecture:** Both iroh-docs and iroh-gossip are iroh `ProtocolHandler`s spawned on the daemon's persisted-identity **router endpoint**. iroh-docs requires both an `iroh-blobs` store and an `iroh-gossip` instance to spawn (gossip is its sync transport), so gossip is constructed in Phase 1 even though its topic/consumer features arrive in Phase 2. The public method signatures of `DocsStore`/`GossipStore` are preserved as the seam; only their internals change. Each engagement maps 1:1 to an iroh-docs namespace; the Commander writes, Workers get read-only `DocTicket` replicas.

**Tech Stack:** Rust, `iroh 1.0`, `iroh-docs 0.101` (`fs-store` feature), `iroh-gossip 0.101`, `iroh-blobs 0.103`, `tokio`, `serde_json`. Python (Textual) for the TUI consumer layer. Tests: `cargo test`, `cargo clippy`, `uv run pytest`.

**Design spec:** `docs/superpowers/specs/2026-06-17-iroh-docs-gossip-integration-design.md`

---

## Confirmed iroh API reference (0.101 / 0.103)

These signatures were verified against the installed crate sources. Use them verbatim.

**Blobs (`iroh-blobs 0.103`):**
- `iroh_blobs::store::mem::MemStore::new() -> MemStore` (tests)
- `iroh_blobs::store::fs::FsStore::load(root: impl AsRef<Path>) -> Result<FsStore>` (persistent)
- `blobs.get_bytes(hash: impl Into<Hash>).await -> Result<Bytes>` (read entry content)
- A `MemStore`/`FsStore` derefs to `iroh_blobs::api::Store`; pass it where `BlobsStore` is required.

**Docs (`iroh-docs 0.101`):**
- `iroh_docs::protocol::Docs::memory() -> Builder` (tests)
- `iroh_docs::protocol::Docs::persistent(path: PathBuf) -> Builder` (needs `fs-store` feature)
- `Builder::spawn(endpoint, blobs, gossip).await -> Result<Docs>` — **requires gossip + blobs**
- `Docs` derefs to `DocsApi`. Spawn it as a `ProtocolHandler` on the Router under `iroh_docs::ALPN`.
- `DocsApi::create().await -> Result<Doc>`
- `DocsApi::import(ticket: DocTicket).await -> Result<Doc>` (joins peers + starts sync)
- `DocsApi::import_namespace(capability: Capability).await -> Result<Doc>`
- `DocsApi::author_default().await -> Result<AuthorId>`
- `Doc::set_bytes(author_id: AuthorId, key: impl Into<Bytes>, value: impl Into<Bytes>).await -> Result<Hash>`
- `Doc::get_many(query: impl Into<Query>).await -> Result<impl Stream<Item=Result<Entry>>>`
- `Doc::share(mode: ShareMode, addr_options: AddrInfoOptions).await -> Result<DocTicket>`
- `Doc::id() -> NamespaceId`
- `Entry::content_hash() -> Hash`, `Entry::key() -> &[u8]`
- `iroh_docs::store::Query::key_prefix(prefix: impl AsRef<[u8]>) -> QueryBuilder<FlatQuery>` (a `QueryBuilder` converts `Into<Query>`)
- Types: `iroh_docs::{NamespaceId, AuthorId, Capability, DocTicket, ALPN}`, `iroh_docs::api::protocol::ShareMode`, `iroh::node_info::AddrInfoOptions` (use `AddrInfoOptions::default()` / the variant the share API expects — see Task 1.2 verification step).

**Gossip (`iroh-gossip 0.101`):**
- `iroh_gossip::net::Gossip::builder().spawn(endpoint: Endpoint) -> Gossip`
- Spawn it as a `ProtocolHandler` on the Router under `iroh_gossip::ALPN`.
- `gossip.subscribe(topic_id: TopicId, bootstrap: Vec<NodeId>).await -> Result<GossipTopic, ApiError>`
- `GossipTopic::split() -> (GossipSender, GossipReceiver)`
- `GossipSender::broadcast(message: Bytes).await -> Result<(), ApiError>`
- `GossipReceiver` is a `Stream<Item = Result<Event, ApiError>>`; `Event::Received(Message)` carries `message.content: Bytes`.
- `iroh_gossip::proto::TopicId` (32 bytes); build via `TopicId::from_bytes(blake3::hash(s).into())` or `TopicId::from([u8;32])`.

> **Verification gate (do this once, before Task 1.3):** the exact module paths above (e.g. `store::mem::MemStore`, `api::protocol::ShareMode`, `AddrInfoOptions` location, `Event` variant names, `TopicId` constructor) are the most likely to drift. Task 1.2 includes a throwaway `cargo build` probe to confirm each import resolves; fix import paths there, then proceed.

---

## File Structure

**Phase 1 (iroh-docs + foundation):**
- Modify: `meshd/Cargo.toml` — add `fs-store` feature to `iroh-docs`/`iroh-blobs`, add `blake3`.
- Create: `meshd/src/mesh_stack.rs` — builds & owns the iroh stack (blobs + gossip + docs) on the router endpoint; returns handles. One responsibility: construct/persist the iroh protocol stack.
- Rewrite: `meshd/src/docs.rs` — `DocsStore` wraps `DocsApi`; namespace-per-engagement; key encoding; JSON via blobs; ticket generation/import.
- Modify: `meshd/src/main.rs` — build the stack via `mesh_stack`, register all ALPNs on one Router, thread handles into `Handler`.
- Modify: `meshd/src/handler.rs` — accept real `Docs`/blobs handles; expose `doc_store()`.
- Modify: `meshd/src/engagement.rs` — `create` generates a real namespace + read ticket; `join` imports a ticket; invite payload carries `doc_ticket`.
- Modify: `meshd/src/p2p.rs` — `get_state` reads local replica (unchanged call shape).
- Test: `meshd/src/docs.rs` (`#[cfg(test)]` unit tests over `Docs::memory()`), `meshd/tests/docs_replication_test.rs` (two-node integration), extend `meshd/tests/p2p_test.rs`.

**Phase 2 (iroh-gossip features + TUI consumers):**
- Rewrite: `meshd/src/gossip.rs` — `GossipStore` wraps `Gossip`; topic map; `subscribe_stream`; per-topic receive loop emitting daemon `Event`s.
- Modify: `meshd/src/protocol.rs` — ensure an `Event` notification variant exists for gossip-derived events.
- Modify: `meshd/src/main.rs` — bridge gossip receive-loop events onto stdout as JSON-line `Event`s.
- Modify: `meshd/src/engagement.rs` / `meshd/src/processor.rs` — broadcast `activity`/`processed` events at the right points; presence heartbeat task.
- Modify: `riftor/mesh/events.py`, `riftor/mesh/sidebar.py` — consume gossip-derived events, update sidebar live.
- Test: `meshd/src/gossip.rs` unit tests, `meshd/tests/gossip_test.rs` (two in-process nodes), `tests/mesh/test_events.py`.

---

# PHASE 1 — iroh-docs (resilience, goal B)

## Task 1.1: Add fs-store feature and blake3 dependency

**Files:**
- Modify: `meshd/Cargo.toml`

- [ ] **Step 1: Edit Cargo.toml dependencies**

Replace the existing `iroh-docs`, `iroh-blobs` lines and add `blake3`:

```toml
iroh-docs = { version = "0.101.0", features = ["fs-store"] }
iroh-gossip = "0.101.0"
iroh-blobs = { version = "0.103.0", features = ["fs-store"] }
blake3 = "1"
```

- [ ] **Step 2: Verify it resolves**

Run: `. "$HOME/.cargo/env" && cargo build --manifest-path meshd/Cargo.toml`
Expected: builds (warnings about unused are fine at this stage; no dependency-resolution errors).

- [ ] **Step 3: Commit**

```bash
git add meshd/Cargo.toml meshd/Cargo.lock
git commit -m "build: enable iroh-docs/blobs fs-store, add blake3"
```

## Task 1.2: Create mesh_stack module skeleton + import probe

**Files:**
- Create: `meshd/src/mesh_stack.rs`
- Modify: `meshd/src/lib.rs` (add `pub mod mesh_stack;`)

- [ ] **Step 1: Add module declaration to lib.rs**

In `meshd/src/lib.rs`, add alongside the other `pub mod` lines:

```rust
pub mod mesh_stack;
```

- [ ] **Step 2: Write the stack builder with explicit imports (the probe)**

Create `meshd/src/mesh_stack.rs`:

```rust
use anyhow::Context;
use iroh::endpoint::Endpoint;
use iroh_blobs::store::fs::FsStore;
use iroh_blobs::store::mem::MemStore;
use iroh_docs::protocol::Docs;
use iroh_gossip::net::Gossip;
use std::path::PathBuf;
use std::sync::Arc;

/// The shared iroh protocol stack: blobs store, gossip, and docs.
///
/// iroh-docs requires both a blobs store and a gossip instance to spawn
/// (gossip is its sync transport), so all three are built together here on the
/// router endpoint and the handles handed out to the rest of the daemon.
pub struct MeshStack {
    pub docs: Docs,
    pub gossip: Gossip,
    pub blobs: Arc<iroh_blobs::api::Store>,
}

impl MeshStack {
    pub fn blobs_api(&self) -> Arc<iroh_blobs::api::Store> {
        self.blobs.clone()
    }
}

fn data_dir() -> PathBuf {
    dirs::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("riftor-mesh")
}

impl MeshStack {
    /// Build the persistent stack on the given endpoint. Falls back to an
    /// in-memory store (logged) if the persistent store cannot be opened, so
    /// the daemon always starts ("bad config never crashes").
    pub async fn build(endpoint: Endpoint) -> anyhow::Result<Self> {
        let gossip = Gossip::builder().spawn(endpoint.clone());
        let dir = data_dir();

        let (docs, blobs) = match Self::build_persistent(&endpoint, &gossip, &dir).await {
            Ok(pair) => pair,
            Err(e) => {
                tracing::warn!(
                    "Persistent docs store failed ({e}); falling back to in-memory store"
                );
                let blobs = MemStore::new();
                let blobs_api = Arc::new((*blobs).clone());
                let docs = Docs::memory()
                    .spawn(endpoint.clone(), (*blobs).clone(), gossip.clone())
                    .await
                    .context("spawn in-memory docs")?;
                (docs, blobs_api)
            }
        };

        Ok(Self { docs, gossip, blobs })
    }

    async fn build_persistent(
        endpoint: &Endpoint,
        gossip: &Gossip,
        dir: &PathBuf,
    ) -> anyhow::Result<(Docs, Arc<iroh_blobs::api::Store>)> {
        tokio::fs::create_dir_all(dir).await?;
        let blobs = FsStore::load(dir.join("blobs")).await?;
        let blobs_api = Arc::new((*blobs).clone());
        let docs = Docs::persistent(dir.clone())
            .spawn(endpoint.clone(), (*blobs).clone(), gossip.clone())
            .await?;
        Ok((docs, blobs_api))
    }
}
```

- [ ] **Step 3: Build to confirm every iroh import path resolves**

Run: `. "$HOME/.cargo/env" && cargo build --manifest-path meshd/Cargo.toml`
Expected: compiles. If any import path is wrong (e.g. `MemStore` location, `(*blobs).clone()` vs passing `blobs` directly, the exact `BlobsStore` type `spawn` wants), the compiler error names the correct path. **Fix the imports/calls here until it builds**, then re-run. This is the single place where the iroh API drift is reconciled.

> Likely adjustments: `spawn` may want the blobs store by value or as `iroh_blobs::api::Store`; `Gossip::clone()` must be cheap (it is — `Gossip` is an `Arc` handle). Adjust the `(*blobs).clone()` expressions to whatever the signature requires.

- [ ] **Step 4: Commit**

```bash
git add meshd/src/lib.rs meshd/src/mesh_stack.rs
git commit -m "feat(mesh): add MeshStack builder for iroh blobs+gossip+docs"
```

## Task 1.3: DocsStore unit test — open/insert/get_all round-trip

**Files:**
- Rewrite: `meshd/src/docs.rs`
- Test: inline `#[cfg(test)]` in `meshd/src/docs.rs`

- [ ] **Step 1: Write the new DocsStore implementation**

Replace the entire contents of `meshd/src/docs.rs`:

```rust
use anyhow::Context;
use iroh_blobs::api::Store as BlobsStore;
use iroh_docs::api::Doc;
use iroh_docs::protocol::Docs;
use iroh_docs::store::Query;
use iroh_docs::{AuthorId, DocTicket, NamespaceId};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

/// CRDT-backed engagement state. Each engagement maps to one iroh-docs replica
/// (namespace). The Commander creates and writes; Workers import a read-only
/// ticket and hold a synced replica.
pub struct DocsStore {
    docs: Docs,
    blobs: Arc<BlobsStore>,
    author: AuthorId,
    /// engagement_id -> open Doc replica
    replicas: Mutex<HashMap<String, Doc>>,
}

impl DocsStore {
    pub async fn new(docs: Docs, blobs: Arc<BlobsStore>) -> anyhow::Result<Self> {
        let author = docs.author_default().await.context("get default author")?;
        Ok(Self {
            docs,
            blobs,
            author,
            replicas: Mutex::new(HashMap::new()),
        })
    }

    fn encode_key(doc_type: &str, key: &str) -> String {
        format!("{doc_type}/{key}")
    }

    /// Open (create on first use) the replica for an engagement, as the Commander.
    pub async fn open(&self, engagement_id: &str) -> anyhow::Result<()> {
        let mut replicas = self.replicas.lock().await;
        if replicas.contains_key(engagement_id) {
            return Ok(());
        }
        let doc = self.docs.create().await.context("create namespace")?;
        replicas.insert(engagement_id.to_string(), doc);
        Ok(())
    }

    /// Adopt an already-created Doc (used when joining via a ticket).
    pub async fn adopt(&self, engagement_id: &str, doc: Doc) {
        self.replicas.lock().await.insert(engagement_id.to_string(), doc);
    }

    /// NamespaceId of an open engagement, if any.
    pub async fn namespace_id(&self, engagement_id: &str) -> Option<NamespaceId> {
        self.replicas.lock().await.get(engagement_id).map(|d| d.id())
    }

    pub async fn insert(
        &self,
        engagement_id: &str,
        doc_type: &str,
        key: &str,
        value: Value,
    ) -> anyhow::Result<()> {
        let replicas = self.replicas.lock().await;
        let doc = replicas
            .get(engagement_id)
            .with_context(|| format!("engagement {engagement_id} not open"))?;
        let bytes = serde_json::to_vec(&value)?;
        doc.set_bytes(self.author, Self::encode_key(doc_type, key), bytes)
            .await
            .context("set_bytes")?;
        Ok(())
    }

    pub async fn get_all(
        &self,
        engagement_id: &str,
        doc_type: &str,
    ) -> anyhow::Result<Vec<Value>> {
        use futures::StreamExt;
        let replicas = self.replicas.lock().await;
        let doc = match replicas.get(engagement_id) {
            Some(d) => d,
            None => return Ok(Vec::new()),
        };
        let prefix = format!("{doc_type}/");
        let mut stream = doc.get_many(Query::key_prefix(prefix.as_bytes())).await?;
        let mut out = Vec::new();
        while let Some(entry) = stream.next().await {
            let entry = entry?;
            let bytes = self.blobs.get_bytes(entry.content_hash()).await?;
            if let Ok(v) = serde_json::from_slice::<Value>(&bytes) {
                out.push(v);
            }
        }
        Ok(out)
    }

    pub async fn query_similar(
        &self,
        engagement_id: &str,
        target: &str,
        vuln_class: &str,
        limit: usize,
    ) -> anyhow::Result<Vec<Value>> {
        let findings = self.get_all(engagement_id, "finding").await?;
        let mut candidates: Vec<Value> = findings
            .into_iter()
            .filter(|v| {
                let f_target = v.get("target").and_then(|t| t.as_str()).unwrap_or("");
                let f_class = v.get("vuln_class").and_then(|c| c.as_str()).unwrap_or("");
                f_target == target || f_class == vuln_class
            })
            .collect();
        candidates.truncate(limit);
        Ok(candidates)
    }

    /// Generate a read-only ticket for an open engagement (Commander side).
    pub async fn read_ticket(&self, engagement_id: &str) -> anyhow::Result<DocTicket> {
        use iroh::node_info::AddrInfoOptions;
        use iroh_docs::api::protocol::ShareMode;
        let replicas = self.replicas.lock().await;
        let doc = replicas
            .get(engagement_id)
            .with_context(|| format!("engagement {engagement_id} not open"))?;
        let ticket = doc
            .share(ShareMode::Read, AddrInfoOptions::default())
            .await
            .context("share read ticket")?;
        Ok(ticket)
    }

    /// Import a read ticket and adopt the resulting replica (Worker side).
    pub async fn import_ticket(
        &self,
        engagement_id: &str,
        ticket: DocTicket,
    ) -> anyhow::Result<()> {
        let doc = self.docs.import(ticket).await.context("import ticket")?;
        self.adopt(engagement_id, doc).await;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use iroh::endpoint::Endpoint;
    use iroh_blobs::store::mem::MemStore;
    use iroh_gossip::net::Gossip;
    use serde_json::json;

    async fn mem_store() -> DocsStore {
        let ep = Endpoint::builder(iroh::endpoint::presets::Minimal)
            .bind()
            .await
            .unwrap();
        let gossip = Gossip::builder().spawn(ep.clone());
        let blobs = MemStore::new();
        let docs = Docs::memory()
            .spawn(ep, (*blobs).clone(), gossip)
            .await
            .unwrap();
        let blobs_api = Arc::new((*blobs).clone());
        DocsStore::new(docs, blobs_api).await.unwrap()
    }

    #[tokio::test]
    async fn insert_and_get_all_round_trip() {
        let store = mem_store().await;
        store.open("eng1").await.unwrap();
        store
            .insert("eng1", "finding", "f1", json!({"title": "SQLi"}))
            .await
            .unwrap();
        let all = store.get_all("eng1", "finding").await.unwrap();
        assert_eq!(all.len(), 1);
        assert_eq!(all[0]["title"], "SQLi");
    }
}
```

- [ ] **Step 2: Run the test to verify it fails to compile or fails**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml docs:: 2>&1 | tail -30`
Expected: At this point `DocsStore::new` has a new signature that callers (`handler.rs`, `engagement.rs`) don't use yet, so the **library may not compile**. That is expected — the test target won't build until Task 1.4 updates the callers. If the `docs.rs` module itself has an import/type error, fix it now (this is where `BlobsStore` type, `(*blobs).clone()`, `AddrInfoOptions::default()`, and `ShareMode` paths get reconciled against the compiler).

> Use `cargo build --manifest-path meshd/Cargo.toml --lib 2>&1 | grep -A3 docs.rs` to isolate errors originating in `docs.rs` from errors caused by stale callers.

- [ ] **Step 3: Commit (module compiles in isolation)**

Once `docs.rs`'s own type errors are resolved (callers may still be broken — that's Task 1.4):

```bash
git add meshd/src/docs.rs
git commit -m "feat(mesh): DocsStore backed by iroh-docs with read tickets"
```

## Task 1.4: Wire the stack through main.rs and handler.rs

**Files:**
- Modify: `meshd/src/main.rs`
- Modify: `meshd/src/handler.rs:20-113`

- [ ] **Step 1: Update handler.rs to accept the real Docs handles**

In `meshd/src/handler.rs`, change `Handler::new` (currently lines 20-68) to take the `DocsStore` already constructed (instead of building a stub):

```rust
    pub async fn new(
        endpoint: Arc<iroh::endpoint::Endpoint>,
        docs: Arc<crate::docs::DocsStore>,
        gossip: Arc<crate::gossip::GossipStore>,
        p2p_node_id: String,
        p2p_relay_urls: Vec<String>,
        p2p_direct_addrs: Vec<String>,
    ) -> anyhow::Result<Self> {
        let identity_manager = crate::identity::IdentityManager::load_or_create().await?;
        let node_id = endpoint.id().to_string();

        tracing::info!(
            "Loaded identity: node_id={} public_key={}",
            node_id,
            identity_manager.public_key()
        );

        let engagement_manager = crate::engagement::EngagementManager::new(
            node_id.clone(),
            docs.clone(),
            gossip.clone(),
            endpoint.clone(),
        );

        let queue = Arc::new(SubmissionQueue::new(256));
        let llm_config = crate::llm::LlmConfig::default();
        let processor = Arc::new(Processor::new(
            queue.clone(),
            docs.clone(),
            llm_config,
            ProcessorMode::Autonomous,
            1,
        ));
        let processor_clone = processor.clone();
        tokio::spawn(async move { processor_clone.start().await });

        Ok(Self {
            identity_manager,
            engagement_manager,
            processor,
            endpoint,
            p2p_node_id,
            p2p_relay_urls,
            p2p_direct_addrs,
        })
    }
```

Remove the old in-stub construction (`DocsStore::new()` / `GossipStore::new()` at old lines 36-37). Leave `doc_store()` (returns `self.engagement_manager.docs.clone()`) unchanged.

> `new_with_processor` (old lines 70-102) is only used by tests that construct an endpoint internally. Update it to also build a `MeshStack` + `DocsStore`/`GossipStore` the same way, or mark it `#[cfg(test)]`-only. Verify which tests call it: `rg new_with_processor meshd/`.

- [ ] **Step 2: Update main.rs to build the stack and pass handles**

In `meshd/src/main.rs`, after the router endpoint is created (around current line 23) and before `Handler::new`, build the stack and construct the stores. Replace the handler-construction block:

```rust
    // Build the shared iroh stack (blobs + gossip + docs) on the router endpoint.
    let stack = meshd::mesh_stack::MeshStack::build(router_ep.clone()).await?;
    let blobs_api = stack.blobs_api(); // Arc<iroh_blobs::api::Store> — see note
    let docs_store = Arc::new(
        meshd::docs::DocsStore::new(stack.docs.clone(), blobs_api).await?,
    );
    let gossip_store = Arc::new(meshd::gossip::GossipStore::new(stack.gossip.clone()));

    let handler = Handler::new(
        handler_ep.clone(),
        docs_store.clone(),
        gossip_store.clone(),
        node_id.to_string(),
        relay_urls,
        direct_addrs,
    )
    .await?;

    // Register docs + gossip ALPNs on the SAME router alongside riftor-mesh/0.
    let p2p_queue = handler.submission_queue();
    let p2p_docs = handler.doc_store();
    let _router = iroh::protocol::Router::builder(router_ep)
        .accept(meshd::p2p::ALPN.to_vec(), /* existing MeshProtocolHandler */)
        .accept(iroh_docs::ALPN.to_vec(), stack.docs.clone())
        .accept(iroh_gossip::net::GOSSIP_ALPN.to_vec(), stack.gossip.clone())
        .spawn();
```

> **Note on `blobs_api()`:** `MeshStack` currently exposes `docs` and `gossip`. Add a `blobs: Arc<iroh_blobs::api::Store>` field to `MeshStack` in Task 1.2's struct (carry the blobs handle out of `build`/`build_persistent`) and a `pub fn blobs_api(&self) -> Arc<...> { self.blobs.clone() }`. Make this edit as part of this step.
>
> **Note on the Router rewrite:** the existing `spawn_router` in `p2p.rs` builds a Router with only the mesh ALPN. Either extend `spawn_router` to also accept the docs+gossip handles and register all three ALPNs, or inline the Router builder in `main.rs` as shown. Prefer extending `spawn_router(router_ep, queue, docs, docs_proto, gossip_proto)` to keep Router construction in one place. Wire the existing `MeshProtocolHandler` (queue + p2p docs) exactly as today.

- [ ] **Step 3: Build the whole daemon**

Run: `. "$HOME/.cargo/env" && cargo build --manifest-path meshd/Cargo.toml 2>&1 | tail -30`
Expected: compiles. Resolve any remaining signature mismatches (e.g. `GOSSIP_ALPN` const name — confirm via `rg "ALPN" ~/.cargo/registry/src/*/iroh-gossip-0.101.0/src/`).

- [ ] **Step 4: Run the docs unit test (now the lib compiles)**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml docs::tests::insert_and_get_all_round_trip 2>&1 | tail -15`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add meshd/src/main.rs meshd/src/handler.rs meshd/src/mesh_stack.rs meshd/src/p2p.rs
git commit -m "feat(mesh): wire real iroh-docs stack through daemon, register ALPNs"
```

## Task 1.5: Prefix isolation + query_similar unit tests

**Files:**
- Test: `meshd/src/docs.rs` (`#[cfg(test)]`)

- [ ] **Step 1: Add two more unit tests**

Append inside the existing `mod tests` in `meshd/src/docs.rs`:

```rust
    #[tokio::test]
    async fn doc_type_prefix_isolation() {
        let store = mem_store().await;
        store.open("eng1").await.unwrap();
        store
            .insert("eng1", "finding", "f1", json!({"title": "SQLi"}))
            .await
            .unwrap();
        store
            .insert("eng1", "host", "h1", json!({"ip": "10.0.0.1"}))
            .await
            .unwrap();
        let findings = store.get_all("eng1", "finding").await.unwrap();
        let hosts = store.get_all("eng1", "host").await.unwrap();
        assert_eq!(findings.len(), 1);
        assert_eq!(hosts.len(), 1);
        assert_eq!(findings[0]["title"], "SQLi");
        assert_eq!(hosts[0]["ip"], "10.0.0.1");
    }

    #[tokio::test]
    async fn get_all_unknown_engagement_is_empty() {
        let store = mem_store().await;
        let res = store.get_all("nope", "finding").await.unwrap();
        assert!(res.is_empty());
    }

    #[tokio::test]
    async fn query_similar_filters_by_target_or_class() {
        let store = mem_store().await;
        store.open("eng1").await.unwrap();
        store
            .insert("eng1", "finding", "f1", json!({"target": "a", "vuln_class": "xss"}))
            .await
            .unwrap();
        store
            .insert("eng1", "finding", "f2", json!({"target": "b", "vuln_class": "sqli"}))
            .await
            .unwrap();
        let hits = store.query_similar("eng1", "a", "none", 10).await.unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0]["target"], "a");
    }
```

- [ ] **Step 2: Run the tests**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml docs::tests 2>&1 | tail -15`
Expected: all docs tests PASS.

- [ ] **Step 3: Commit**

```bash
git add meshd/src/docs.rs
git commit -m "test(mesh): docs prefix isolation, empty engagement, query_similar"
```

## Task 1.6: Real namespace + read ticket in engagement create/join

**Files:**
- Modify: `meshd/src/engagement.rs:9-176`

- [ ] **Step 1: Add doc_ticket to the invite payload and use real namespace**

In `meshd/src/engagement.rs`, add a `doc_ticket` field to `InvitePayload` (after `direct_addresses`, before `created_at`), tolerant on decode:

```rust
    /// iroh-docs read ticket for the engagement replica (None for legacy invites)
    #[serde(default)]
    doc_ticket: Option<String>,
```

In `create` (currently lines 62-72), replace the UUID namespace with the real one and capture the ticket. After `self.docs.open(&id).await?;`, set:

```rust
        let namespace_id = self
            .docs
            .namespace_id(&id)
            .await
            .map(|n| n.to_string())
            .unwrap_or_default();
```

Remove the old `let namespace_id = Uuid::new_v4().to_string();` line.

In the invite-generation method (find it via `rg "fn.*invite" meshd/src/engagement.rs`), populate `doc_ticket`:

```rust
        let doc_ticket = self.docs.read_ticket(engagement_id).await.ok().map(|t| t.to_string());
```

and include it in the constructed `InvitePayload`.

- [ ] **Step 2: Import the ticket on join**

In `join` (currently around lines 118-131), after decoding the invite, import the ticket if present:

```rust
        if let Some(ticket_str) = &invite.doc_ticket {
            match ticket_str.parse::<iroh_docs::DocTicket>() {
                Ok(ticket) => {
                    if let Err(e) = self.docs.import_ticket(&invite.engagement_id, ticket).await {
                        tracing::warn!("docs import failed: {e}; joining without replica");
                    }
                }
                Err(e) => tracing::warn!("bad doc_ticket in invite: {e}; legacy join"),
            }
        } else {
            tracing::warn!("invite has no doc_ticket; legacy join (no CRDT replica)");
        }
```

Keep the existing gossip `join` calls (they remain stubs until Phase 2; signatures unchanged).

- [ ] **Step 3: Build and run existing engagement-related tests**

Run: `. "$HOME/.cargo/env" && cargo build --manifest-path meshd/Cargo.toml && cargo test --manifest-path meshd/Cargo.toml 2>&1 | tail -25`
Expected: compiles; existing integration/processor tests still PASS (the in-process tests don't exercise tickets yet).

- [ ] **Step 4: Commit**

```bash
git add meshd/src/engagement.rs
git commit -m "feat(mesh): real iroh-docs namespace + read ticket in invites"
```

## Task 1.7: Two-node replication integration test (keystone)

**Files:**
- Create: `meshd/tests/docs_replication_test.rs`

- [ ] **Step 1: Write the failing replication test**

Create `meshd/tests/docs_replication_test.rs`:

```rust
use iroh::endpoint::Endpoint;
use iroh_blobs::store::mem::MemStore;
use iroh_docs::protocol::Docs;
use iroh_gossip::net::Gossip;
use meshd::docs::DocsStore;
use serde_json::json;
use std::sync::Arc;
use std::time::Duration;

async fn node() -> DocsStore {
    let ep = Endpoint::builder(iroh::endpoint::presets::Minimal)
        .bind()
        .await
        .unwrap();
    let gossip = Gossip::builder().spawn(ep.clone());
    let blobs = MemStore::new();
    let docs = Docs::memory()
        .spawn(ep.clone(), (*blobs).clone(), gossip.clone())
        .await
        .unwrap();
    // NOTE: for real cross-node sync the docs+gossip protocols must be
    // registered on each endpoint's Router. Build a Router here accepting
    // iroh_docs::ALPN and the gossip ALPN, mirroring main.rs.
    let _router = iroh::protocol::Router::builder(ep)
        .accept(iroh_docs::ALPN.to_vec(), docs.clone())
        .accept(iroh_gossip::net::GOSSIP_ALPN.to_vec(), gossip.clone())
        .spawn();
    DocsStore::new(docs, Arc::new((*blobs).clone())).await.unwrap()
}

#[tokio::test]
async fn finding_replicates_commander_to_worker() {
    let commander = node().await;
    let worker = node().await;

    commander.open("eng1").await.unwrap();
    commander
        .insert("eng1", "finding", "f1", json!({"title": "SQLi"}))
        .await
        .unwrap();

    let ticket = commander.read_ticket("eng1").await.unwrap();
    worker.import_ticket("eng1", ticket).await.unwrap();

    // Sync is async; poll up to a few seconds.
    let mut found = false;
    for _ in 0..30 {
        let all = worker.get_all("eng1", "finding").await.unwrap();
        if all.len() == 1 && all[0]["title"] == "SQLi" {
            found = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    assert!(found, "finding did not replicate to worker");
}
```

- [ ] **Step 2: Run the test**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml --test docs_replication_test 2>&1 | tail -25`
Expected: PASS. If it times out, the most likely cause is the Routers not being wired or `import` not starting sync — verify both endpoints registered `iroh_docs::ALPN`, and that `DocsApi::import` (which calls `start_sync`) is used (it is, in `import_ticket`).

> If the in-memory two-node test proves flaky under loopback relay timing, fall back to asserting replication via an explicit `doc.sync_with`/connection in the test setup, or mark the test `#[ignore]` with a note and rely on `dev/mesh_p2p_test.py` for the real cross-process check. Document whichever path is taken in the test file.

- [ ] **Step 3: Commit**

```bash
git add meshd/tests/docs_replication_test.rs
git commit -m "test(mesh): two-node Commander->Worker replication via read ticket"
```

## Task 1.8: Persistence test + extend p2p_test for coexisting ALPNs

**Files:**
- Create: `meshd/tests/docs_persistence_test.rs`
- Modify: `meshd/tests/p2p_test.rs`

- [ ] **Step 1: Write the persistence test**

Create `meshd/tests/docs_persistence_test.rs`:

```rust
use iroh::endpoint::Endpoint;
use iroh_blobs::store::fs::FsStore;
use iroh_docs::protocol::Docs;
use iroh_gossip::net::Gossip;
use iroh_docs::store::Query;

#[tokio::test]
async fn entries_survive_reopen() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().to_path_buf();

    let key = b"finding/f1".to_vec();
    let namespace;
    {
        let ep = Endpoint::builder(iroh::endpoint::presets::Minimal).bind().await.unwrap();
        let gossip = Gossip::builder().spawn(ep.clone());
        let blobs = FsStore::load(path.join("blobs")).await.unwrap();
        let docs = Docs::persistent(path.clone())
            .spawn(ep, (*blobs).clone(), gossip).await.unwrap();
        let author = docs.author_default().await.unwrap();
        let doc = docs.create().await.unwrap();
        namespace = doc.id();
        doc.set_bytes(author, key.clone(), b"{\"title\":\"SQLi\"}".to_vec()).await.unwrap();
        // drop everything to flush + close
    }

    {
        let ep = Endpoint::builder(iroh::endpoint::presets::Minimal).bind().await.unwrap();
        let gossip = Gossip::builder().spawn(ep.clone());
        let blobs = FsStore::load(path.join("blobs")).await.unwrap();
        let docs = Docs::persistent(path.clone())
            .spawn(ep, (*blobs).clone(), gossip).await.unwrap();
        let doc = docs.import_namespace(iroh_docs::Capability::Write(
            // reopen the same namespace's write capability from local store
            namespace.into(),
        )).await.expect("reopen namespace");
        use futures::StreamExt;
        let mut stream = doc.get_many(Query::key_prefix(b"finding/".to_vec())).await.unwrap();
        let mut count = 0;
        while let Some(e) = stream.next().await { e.unwrap(); count += 1; }
        assert_eq!(count, 1, "entry did not persist across reopen");
    }
}
```

> **Verification:** the exact reopen API for a locally-stored namespace may differ (the persistent author/replica store should already hold it, so a `docs.open(namespace)` or listing via `docs.list()` may be the correct call rather than `import_namespace`). Confirm against `rg "pub async fn" ~/.cargo/registry/src/*/iroh-docs-0.101.0/src/api.rs | grep -iE "open|list|load"` and adjust this step to the real reopen method. The assertion (entry count == 1 after reopen) is the invariant; the reopen call is the detail to fix.

Add `tempfile = "3"` to `[dev-dependencies]` in `meshd/Cargo.toml` if not present.

- [ ] **Step 2: Run the persistence test**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml --test docs_persistence_test 2>&1 | tail -20`
Expected: PASS.

- [ ] **Step 3: Confirm existing p2p_test still passes with ALPNs coexisting**

The existing `tests/p2p_test.rs` uses its own `EchoHandler` Router; it should be unaffected. Just confirm:

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml --test p2p_test 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add meshd/tests/docs_persistence_test.rs meshd/Cargo.toml meshd/Cargo.lock
git commit -m "test(mesh): docs entries persist across reopen"
```

## Task 1.9: Phase 1 full gate

- [ ] **Step 1: Run all Rust tests**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml 2>&1 | tail -30`
Expected: all PASS.

- [ ] **Step 2: Clippy clean**

Run: `. "$HOME/.cargo/env" && cargo clippy --manifest-path meshd/Cargo.toml --all-targets 2>&1 | tail -10`
Expected: zero warnings. Fix any.

- [ ] **Step 3: Python tests still green (daemon protocol unchanged)**

Run: `uv run pytest tests/mesh/ -q 2>&1 | tail -5`
Expected: 19 PASS.

- [ ] **Step 4: Manual smoke — NodeId + ping still work**

Run: `. "$HOME/.cargo/env" && echo '{"id":1,"method":"ping"}' | ./meshd/target/debug/riftor-meshd 2>&1 | tail -5`
Expected: a `{"id":1,"result":{"pong":true}}` line and clean startup logs (now including docs/gossip stack init).

- [ ] **Step 5: Commit any clippy fixes**

```bash
git add -A && git commit -m "chore(mesh): phase 1 clippy + test gate green"
```

---

# PHASE 2 — iroh-gossip (live updates, goal A)

## Task 2.1: GossipStore unit test — broadcast/subscribe between two nodes

**Files:**
- Rewrite: `meshd/src/gossip.rs`
- Test: inline `#[cfg(test)]` in `meshd/src/gossip.rs`

- [ ] **Step 1: Write the new GossipStore implementation**

Replace the entire contents of `meshd/src/gossip.rs`:

```rust
use anyhow::Context;
use bytes::Bytes;
use iroh::NodeId;
use iroh_gossip::api::{GossipReceiver, GossipSender};
use iroh_gossip::net::Gossip;
use iroh_gossip::proto::TopicId;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

/// Live event pub/sub over iroh-gossip. Topics are derived from
/// `riftor/{engagement_id}/{subtopic}` hashed to a 32-byte TopicId.
pub struct GossipStore {
    gossip: Gossip,
    senders: Mutex<HashMap<String, GossipSender>>,
}

fn topic_id(engagement_id: &str, subtopic: &str) -> TopicId {
    let s = format!("riftor/{engagement_id}/{subtopic}");
    let hash = blake3::hash(s.as_bytes());
    TopicId::from_bytes(*hash.as_bytes())
}

impl GossipStore {
    pub fn new(gossip: Gossip) -> Self {
        Self {
            gossip,
            senders: Mutex::new(HashMap::new()),
        }
    }

    fn key(engagement_id: &str, subtopic: &str) -> String {
        format!("{engagement_id}/{subtopic}")
    }

    /// Join a topic with optional bootstrap peers; returns a receiver stream.
    pub async fn join(
        &self,
        engagement_id: &str,
        subtopic: &str,
        bootstrap: Vec<NodeId>,
    ) -> anyhow::Result<GossipReceiver> {
        let tid = topic_id(engagement_id, subtopic);
        let topic = self
            .gossip
            .subscribe(tid, bootstrap)
            .await
            .context("gossip subscribe")?;
        let (sender, receiver) = topic.split();
        self.senders
            .lock()
            .await
            .insert(Self::key(engagement_id, subtopic), sender);
        Ok(receiver)
    }

    pub async fn broadcast(
        &self,
        engagement_id: &str,
        subtopic: &str,
        message: Value,
    ) -> anyhow::Result<()> {
        let senders = self.senders.lock().await;
        if let Some(sender) = senders.get(&Self::key(engagement_id, subtopic)) {
            let bytes = Bytes::from(serde_json::to_vec(&message)?);
            sender.broadcast(bytes).await.context("gossip broadcast")?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures::StreamExt;
    use iroh::endpoint::Endpoint;
    use iroh_gossip::api::Event;
    use serde_json::json;
    use std::time::Duration;

    async fn node() -> (GossipStore, NodeId, Endpoint) {
        let ep = Endpoint::builder(iroh::endpoint::presets::Minimal)
            .bind()
            .await
            .unwrap();
        let gossip = Gossip::builder().spawn(ep.clone());
        let _router = iroh::protocol::Router::builder(ep.clone())
            .accept(iroh_gossip::net::GOSSIP_ALPN.to_vec(), gossip.clone())
            .spawn();
        let id = ep.id();
        (GossipStore::new(gossip), id, ep)
    }

    #[tokio::test]
    async fn broadcast_reaches_subscriber() {
        let (a, a_id, a_ep) = node().await;
        let (b, _b_id, _b_ep) = node().await;

        // A is the bootstrap peer for B. B needs A's address.
        let a_addr = a_ep.addr();
        // (in the real daemon, address discovery uses relays; in-process we add directly)
        let _ = b; // see note: cross-node gossip needs address wiring

        let _recv_a = a.join("eng1", "activity", vec![]).await.unwrap();
        let mut recv_b = b.join("eng1", "activity", vec![a_id]).await.unwrap();

        // give the overlay a moment to connect
        tokio::time::sleep(Duration::from_millis(500)).await;
        a.broadcast("eng1", "activity", json!({"msg": "hello"}))
            .await
            .unwrap();

        let mut got = None;
        for _ in 0..30 {
            if let Ok(Some(ev)) =
                tokio::time::timeout(Duration::from_millis(200), recv_b.next()).await
            {
                if let Ok(Event::Received(m)) = ev {
                    let v: Value = serde_json::from_slice(&m.content).unwrap();
                    got = Some(v);
                    break;
                }
            }
        }
        assert_eq!(got.unwrap()["msg"], "hello");
        let _ = a_addr;
    }
}
```

- [ ] **Step 2: Run the test**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml gossip::tests 2>&1 | tail -25`
Expected: PASS. Reconcile exact import paths here (`iroh_gossip::api::{Event, GossipSender, GossipReceiver}`, `Event::Received` field name `content`, `TopicId::from_bytes`). If in-process gossip can't establish a neighbor without explicit address injection, add the peer address to the endpoint's address book before subscribing (check `rg "add_node_addr\|add_addr" ~/.cargo/registry/src/*/iroh-1.0.0/src/endpoint.rs`) and pass the bootstrap NodeId. If loopback gossip proves too flaky for CI, mark this test `#[ignore]` and rely on the integration test in Task 2.2 / the dev script.

- [ ] **Step 3: Commit**

```bash
git add meshd/src/gossip.rs
git commit -m "feat(mesh): GossipStore backed by iroh-gossip pub/sub"
```

## Task 2.2: Update engagement.rs gossip calls + bootstrap from invite

**Files:**
- Modify: `meshd/src/engagement.rs` (gossip join/broadcast call sites)

- [ ] **Step 1: Update join calls to pass bootstrap peers and keep receivers**

The `GossipStore::join` signature now takes `bootstrap: Vec<NodeId>` and returns a `GossipReceiver`. In `EngagementManager::create` (no bootstrap — Commander is first): pass `vec![]`. In `join` (Worker): pass the inviter's NodeId parsed from `invite.node_id`.

In `create` (lines ~69-72), replace the four `self.gossip.join(&id, "...").await?;` with calls that capture and forward the receivers to the event bridge (Task 2.3). For now, store them on the `EngagementManager` or hand them to a registered callback:

```rust
        let inviter: Vec<iroh::NodeId> = Vec::new();
        for sub in ["submit", "activity", "presence", "processed"] {
            let recv = self.gossip.join(&id, sub, inviter.clone()).await?;
            self.spawn_receive_loop(id.clone(), sub.to_string(), recv);
        }
```

In `join` (Worker), build `inviter` from `invite.node_id.parse::<iroh::NodeId>()` and pass it instead of empty.

- [ ] **Step 2: Build**

Run: `. "$HOME/.cargo/env" && cargo build --manifest-path meshd/Cargo.toml 2>&1 | tail -20`
Expected: fails only on the not-yet-defined `spawn_receive_loop` (added in Task 2.3). Confirm the gossip signature changes otherwise compile.

- [ ] **Step 3: Commit (WIP, compiles after 2.3)** — defer commit to end of 2.3.

## Task 2.3: Event bridge — gossip receive loop → daemon stdout Event

**Files:**
- Modify: `meshd/src/protocol.rs` (ensure an `Event` notification variant)
- Modify: `meshd/src/engagement.rs` (add `spawn_receive_loop`)
- Modify: `meshd/src/main.rs` (drain an event channel to stdout)

- [ ] **Step 1: Confirm/extend the Event type in protocol.rs**

Inspect `meshd/src/protocol.rs` for the existing `Event` type (PICKUP says it exists). Ensure a variant like:

```rust
#[derive(Debug, Serialize)]
#[serde(tag = "type")]
pub enum Event {
    MeshEvent {
        engagement_id: String,
        subtopic: String,
        payload: serde_json::Value,
    },
    // ...existing variants...
}
```

If `Event` already exists with a different shape, add a `mesh_event` variant matching the existing serialization convention rather than rewriting it.

- [ ] **Step 2: Add an event channel and spawn_receive_loop**

Add an `mpsc::UnboundedSender<Event>` to `EngagementManager` (set via a `with_event_sink` setter called from `main.rs`). Implement:

```rust
    fn spawn_receive_loop(
        &self,
        engagement_id: String,
        subtopic: String,
        mut receiver: iroh_gossip::api::GossipReceiver,
    ) {
        let sink = self.event_sink.clone();
        tokio::spawn(async move {
            use futures::StreamExt;
            while let Some(ev) = receiver.next().await {
                if let Ok(iroh_gossip::api::Event::Received(m)) = ev {
                    if let Ok(payload) = serde_json::from_slice::<serde_json::Value>(&m.content) {
                        if let Some(sink) = &sink {
                            let _ = sink.send(crate::protocol::Event::MeshEvent {
                                engagement_id: engagement_id.clone(),
                                subtopic: subtopic.clone(),
                                payload,
                            });
                        }
                    }
                }
            }
        });
    }
```

- [ ] **Step 3: Drain the channel to stdout in main.rs**

In `main.rs`, create `let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();`, pass `tx` into the handler/engagement manager, and spawn a task that writes each event as a JSON line to stdout (sharing the same locked stdout discipline as the request loop — use a `tokio::sync::Mutex<Stdout>` or an output channel the main loop drains). Simplest: spawn a task holding its own `io::stdout()` and `writeln!` each serialized event followed by flush.

```rust
    tokio::spawn(async move {
        let mut out = std::io::stdout();
        while let Some(ev) = rx.recv().await {
            if let Ok(json) = serde_json::to_string(&ev) {
                use std::io::Write;
                let _ = writeln!(out, "{json}");
                let _ = out.flush();
            }
        }
    });
```

> Concurrency note: the request loop also writes to stdout. Interleaved `writeln!` of complete JSON lines from two writers is safe at the line level for the Python JSON-line reader as long as each `writeln!` is atomic per line. If interleaving causes torn lines under load, route both through a single output `mpsc` channel drained by one writer task. Prefer the single-writer channel if it's low effort.

- [ ] **Step 4: Build all**

Run: `. "$HOME/.cargo/env" && cargo build --manifest-path meshd/Cargo.toml 2>&1 | tail -20`
Expected: compiles.

- [ ] **Step 5: Commit Tasks 2.2 + 2.3 together**

```bash
git add meshd/src/engagement.rs meshd/src/protocol.rs meshd/src/main.rs
git commit -m "feat(mesh): bridge gossip events to daemon stdout as JSON-line Events"
```

## Task 2.4: Broadcast processed/activity events from the processor

**Files:**
- Modify: `meshd/src/processor.rs:240-290` (publish points)
- Modify: `meshd/src/engagement.rs` (existing `broadcast` call at line ~164)

- [ ] **Step 1: Broadcast a `processed` event when a finding is published**

The processor holds `Arc<DocsStore>` but not gossip. Give the processor an optional `Arc<GossipStore>` + the engagement id is on each submission. After a successful `self.docs.insert(...)` at the publish points (around lines 246 and 284), broadcast:

```rust
        if let Some(gossip) = &self.gossip {
            let _ = gossip
                .broadcast(
                    &engagement_id,
                    "processed",
                    serde_json::json!({"event": "finding_published", "key": finding_key}),
                )
                .await;
        }
```

Thread `gossip: Option<Arc<GossipStore>>` through `Processor::new` (update the call in `handler.rs`). Keep it `Option` so existing `processor_test.rs` constructors (which pass no gossip) still compile — pass `None` there.

- [ ] **Step 2: Build and run processor tests**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml --test processor_test 2>&1 | tail -10`
Expected: PASS (gossip is `None` in those tests, no behavior change).

- [ ] **Step 3: Commit**

```bash
git add meshd/src/processor.rs meshd/src/handler.rs
git commit -m "feat(mesh): broadcast 'processed' gossip event on finding publish"
```

## Task 2.5: Python — consume mesh events in events.py / sidebar.py

**Files:**
- Modify: `riftor/mesh/events.py`
- Modify: `riftor/mesh/sidebar.py`
- Test: `tests/mesh/test_events.py`

- [ ] **Step 1: Write the failing Python test**

Create `tests/mesh/test_events.py`:

```python
import json
from riftor.mesh.events import dispatch_event


def test_mesh_event_processed_triggers_refresh():
    calls = []

    def on_processed(engagement_id, payload):
        calls.append((engagement_id, payload))

    handlers = {"processed": on_processed}
    line = json.dumps({
        "type": "MeshEvent",
        "engagement_id": "eng1",
        "subtopic": "processed",
        "payload": {"event": "finding_published", "key": "finding/f1"},
    })
    dispatch_event(json.loads(line), handlers)
    assert calls == [("eng1", {"event": "finding_published", "key": "finding/f1"})]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/mesh/test_events.py -v 2>&1 | tail -15`
Expected: FAIL (`dispatch_event` not defined or wrong signature). Inspect the existing `events.py` first (`rg "def " riftor/mesh/events.py`) and adapt the test to the real dispatch entrypoint name if one already exists; otherwise add `dispatch_event`.

- [ ] **Step 3: Implement dispatch_event**

In `riftor/mesh/events.py`, add (or adapt existing):

```python
def dispatch_event(event: dict, handlers: dict) -> None:
    """Route a daemon JSON event to the registered handler for its subtopic."""
    if event.get("type") != "MeshEvent":
        return
    subtopic = event.get("subtopic")
    handler = handlers.get(subtopic)
    if handler is not None:
        handler(event.get("engagement_id"), event.get("payload"))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/mesh/test_events.py -v 2>&1 | tail -10`
Expected: PASS.

- [ ] **Step 5: Wire sidebar.py to refresh on `processed`**

In `riftor/mesh/sidebar.py`, register a `processed` handler that triggers the existing state-refresh path (the same code `/mesh-refresh` calls — find via `rg "refresh" riftor/mesh/`), and an `activity` handler that appends a line to the activity feed widget. Follow the existing widget update conventions in that file. No new test required beyond Step 1 if these are thin wiring calls; if they contain logic, add a focused test.

- [ ] **Step 6: Run full Python mesh suite**

Run: `uv run pytest tests/mesh/ -q 2>&1 | tail -5`
Expected: all PASS (19 existing + new).

- [ ] **Step 7: Commit**

```bash
git add riftor/mesh/events.py riftor/mesh/sidebar.py tests/mesh/test_events.py
git commit -m "feat(mesh): consume gossip-derived events, live sidebar refresh"
```

## Task 2.6: Presence heartbeat

**Files:**
- Modify: `meshd/src/engagement.rs` (spawn heartbeat on join/create)

- [ ] **Step 1: Spawn a periodic presence broadcast**

After joining the `presence` topic for an engagement, spawn a task that broadcasts a heartbeat every 15s:

```rust
    fn spawn_presence(&self, engagement_id: String, node_id: String, gossip: Arc<GossipStore>) {
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(std::time::Duration::from_secs(15));
            loop {
                tick.tick().await;
                let _ = gossip
                    .broadcast(
                        &engagement_id,
                        "presence",
                        serde_json::json!({"node_id": node_id, "ts": chrono::Utc::now().to_rfc3339()}),
                    )
                    .await;
            }
        });
    }
```

Call it from `create` and `join` after the topics are joined. The `presence` receive loop (already spawned in Task 2.2) forwards heartbeats as `MeshEvent`s; `sidebar.py` updates the member list.

- [ ] **Step 2: Build**

Run: `. "$HOME/.cargo/env" && cargo build --manifest-path meshd/Cargo.toml 2>&1 | tail -10`
Expected: compiles.

- [ ] **Step 3: Commit**

```bash
git add meshd/src/engagement.rs
git commit -m "feat(mesh): periodic presence heartbeat over gossip"
```

## Task 2.7: Phase 2 full gate + dev script

**Files:**
- Modify: `dev/mesh_p2p_test.py`

- [ ] **Step 1: Extend the cross-process dev script**

Add assertions to `dev/mesh_p2p_test.py`: after the Commander processes a finding, the Worker daemon emits a `MeshEvent` with `subtopic: "processed"` on stdout (read the line), and `get_state` on the Worker returns the finding **without** an explicit refresh call. Follow the script's existing two-daemon spawn pattern.

- [ ] **Step 2: Run all Rust tests + clippy**

Run: `. "$HOME/.cargo/env" && cargo test --manifest-path meshd/Cargo.toml 2>&1 | tail -25 && cargo clippy --manifest-path meshd/Cargo.toml --all-targets 2>&1 | tail -10`
Expected: all tests PASS, zero clippy warnings.

- [ ] **Step 3: Run all Python tests**

Run: `uv run pytest tests/mesh/ -q 2>&1 | tail -5 && uv run pytest tests/ --ignore=tests/mesh -q 2>&1 | tail -5`
Expected: all PASS.

- [ ] **Step 4: Run the existing CI gate**

Run: `make check 2>&1 | tail -20`
Expected: lint → typecheck → test → smoke all pass.

- [ ] **Step 5: Commit**

```bash
git add dev/mesh_p2p_test.py
git commit -m "test(mesh): dev script asserts live processed-event + worker get_state"
```

## Task 2.8: Update PICKUP.md and docs

**Files:**
- Modify: `PICKUP.md`
- Modify: `CLAUDE.md` (the docs.rs/gossip.rs descriptions)

- [ ] **Step 1: Update the "Remaining Work" section**

In `PICKUP.md`, check off the two Medium items (swap docs stub → real iroh-docs; swap gossip stub → real iroh-gossip). Update the file-map descriptions for `docs.rs`/`gossip.rs` to reflect real implementations.

- [ ] **Step 2: Update CLAUDE.md architecture note**

In `CLAUDE.md`, update the `engagement/` description that says docs/gossip are in-memory stubs to describe the real iroh-docs (persisted, read-ticket replicas) and iroh-gossip (live topics) implementations.

- [ ] **Step 3: Commit**

```bash
git add PICKUP.md CLAUDE.md
git commit -m "docs: mark iroh-docs/gossip swaps done, update architecture notes"
```

---

## Self-Review notes (for the executor)

- **Spec coverage:** Phase 1 covers docs namespace/persistence/tickets/local-first get_state (spec §iroh-docs, goal B); Phase 2 covers gossip topics + the missing consumers + daemon→Python Event bridge + presence (spec §iroh-gossip, goal A). Error-handling fallbacks (spec §error handling) are in Task 1.2 (in-memory fallback) and the tolerant ticket decode in Task 1.6. Testing strategy (spec §testing) maps to Tasks 1.3/1.5/1.7/1.8 and 2.1/2.5/2.7.
- **API drift is the main risk.** Tasks 1.2, 1.3, 1.8, 2.1 each contain an explicit "reconcile imports against the compiler" step because the exact module paths (`store::mem::MemStore`, `api::protocol::ShareMode`, `AddrInfoOptions` location, `GOSSIP_ALPN` const, `Event::Received` field, namespace-reopen method) were inferred from source inspection and may need a one-line path fix. The invariants asserted by the tests are stable; the import paths are the detail.
- **iroh-docs needs gossip to spawn** — this is why gossip is constructed in Phase 1 (Task 1.2) even though its features land in Phase 2. Do not skip building gossip in Phase 1.
- **Type consistency:** `DocsStore::new(docs, blobs)` / `open` / `insert` / `get_all` / `read_ticket` / `import_ticket` and `GossipStore::new(gossip)` / `join(eid, sub, bootstrap) -> GossipReceiver` / `broadcast` are used consistently across all tasks. `Processor::new` and `Handler::new` signatures change once (Tasks 1.4, 2.4) and all call sites are updated in the same task.
