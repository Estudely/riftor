use iroh::address_lookup::memory::MemoryLookup;
use iroh::endpoint::Endpoint;
use iroh::protocol::Router;
use iroh_blobs::BlobsProtocol;
use iroh_blobs::store::mem::MemStore;
use iroh_docs::protocol::Docs;
use iroh_gossip::net::Gossip;
use meshd::docs::DocsStore;
use serde_json::json;
use std::sync::Arc;
use std::time::Duration;

// `read_ticket` shares with `AddrInfoOptions::default()`, which is the `Id`-only
// variant: the ticket carries the commander's node id but NO addresses. Under the
// `Minimal` endpoint preset there is no relay and no DNS-based address lookup, so a
// worker that only knows the commander's id has no way to dial it and sync would
// never start. To make replication actually happen over loopback we attach a
// `MemoryLookup` to the worker endpoint and teach it the commander's real local
// `EndpointAddr` before importing the ticket. This is the supported, out-of-band
// addressing mechanism in iroh 1.0 (the modern equivalent of `add_node_addr`).
//
// The `Router` (which accepts the docs + gossip + blobs ALPNs) must outlive the
// test, so we return and hold it for the test's duration.
async fn node() -> (DocsStore, Endpoint, MemoryLookup, Router) {
    let lookup = MemoryLookup::new();
    let ep = Endpoint::builder(iroh::endpoint::presets::Minimal)
        .address_lookup(lookup.clone())
        .bind()
        .await
        .unwrap();
    let gossip = Gossip::builder().spawn(ep.clone());
    let blobs = MemStore::new();
    let docs = Docs::memory()
        .spawn(ep.clone(), (*blobs).clone(), gossip.clone())
        .await
        .unwrap();
    // docs+gossip protocols must be registered on each endpoint's Router for sync.
    // The blobs protocol is also required: iroh-docs replicates entry metadata over
    // the docs ALPN, but the actual content blobs are fetched over the blobs ALPN, so
    // without it the worker learns the entry's hash but can never download its value.
    let blobs_protocol = BlobsProtocol::new(&(*blobs).clone(), None);
    let router = iroh::protocol::Router::builder(ep.clone())
        .accept(iroh_docs::ALPN, docs.clone())
        .accept(iroh_gossip::net::GOSSIP_ALPN, gossip.clone())
        .accept(iroh_blobs::ALPN, blobs_protocol)
        .spawn();
    let store = DocsStore::new(docs, Arc::new((*blobs).clone()))
        .await
        .unwrap();
    (store, ep, lookup, router)
}

#[tokio::test]
async fn finding_replicates_commander_to_worker() {
    let (commander, commander_ep, _commander_lookup, _commander_router) = node().await;
    let (worker, _worker_ep, worker_lookup, _worker_router) = node().await;

    commander.open("eng1").await.unwrap();
    commander
        .insert("eng1", "finding", "f1", json!({"title": "SQLi"}))
        .await
        .unwrap();

    let ticket = commander.read_ticket("eng1").await.unwrap();

    // Teach the worker how to reach the commander. The read ticket only carries the
    // commander's node id (AddrInfoOptions::default() == Id), so without this the
    // worker cannot dial the commander and sync would never start.
    let commander_addr = commander_ep.addr();
    worker_lookup.add_endpoint_info(commander_addr);

    worker.import_ticket("eng1", ticket).await.unwrap();

    // Sync is async; poll up to ~10s. Note that iroh-docs replicates the entry
    // metadata (key + content hash) before the underlying blob content arrives, so a
    // mid-sync `get_all` can transiently error with a blob hash mismatch while the
    // content is still being fetched. We treat any such error as "not ready yet" and
    // keep polling, but the success condition is unchanged: the finding must actually
    // appear in the worker's replica with the correct title.
    let mut found = false;
    for _ in 0..50 {
        if let Ok(all) = worker.get_all("eng1", "finding").await {
            if all.len() == 1 && all[0]["title"] == "SQLi" {
                found = true;
                break;
            }
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
    assert!(found, "finding did not replicate to worker");
}
