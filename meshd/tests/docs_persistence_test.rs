use iroh::endpoint::Endpoint;
use iroh::protocol::ProtocolHandler;
use iroh_blobs::store::fs::FsStore;
use iroh_docs::protocol::Docs;
use iroh_docs::store::Query;
use iroh_gossip::net::Gossip;

// Resilience goal B at the storage layer: a Commander that restarts re-opens its
// persisted replica and its findings are still there. Single-node, no network. We
// build the persistent stack (`Docs::persistent` + `FsStore::load`) against one
// tempdir root, write an entry, cleanly shut the stack down to flush + release the
// on-disk redb locks, then reopen from the same dir and assert the entry survived.
//
// SHUTDOWN ORDERING: dropping the `Docs`/`FsStore` handles is NOT sufficient to
// reopen the same directory — the blobs `FsStore` runs a background actor that holds
// an exclusive redb lock on `<root>/blobs` until `shutdown()` completes, so a second
// `FsStore::load` on the same path would block forever waiting for that lock. We
// therefore explicitly `docs.shutdown()` (drains the docs engine + its redb) and
// `blobs.shutdown()` (releases the blobs redb) before reopening.
//
// REOPEN MECHANISM: after reopening the persistent store, `docs.list()` enumerates
// the namespaces retained on disk and `docs.open(namespace)` returns a writable
// `Doc` handle for one of them (the persistent store retains the namespace secret,
// so no out-of-band capability/secret is needed). We assert the namespace is listed
// and that `get_many(key_prefix("finding/"))` over the reopened doc yields exactly
// one entry.
#[tokio::test]
async fn entries_survive_reopen() {
    use futures::StreamExt;

    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().to_path_buf();

    let key = b"finding/f1".to_vec();
    let namespace;
    {
        let ep = Endpoint::builder(iroh::endpoint::presets::Minimal)
            .bind()
            .await
            .unwrap();
        let gossip = Gossip::builder().spawn(ep.clone());
        let blobs = FsStore::load(path.join("blobs")).await.unwrap();
        let docs = Docs::persistent(path.clone())
            .spawn(ep.clone(), (*blobs).clone(), gossip)
            .await
            .unwrap();
        let author = docs.author_default().await.unwrap();
        let doc = docs.create().await.unwrap();
        namespace = doc.id();
        doc.set_bytes(author, key.clone(), b"{\"title\":\"SQLi\"}".to_vec())
            .await
            .unwrap();

        // Cleanly tear the stack down so the on-disk stores flush and release their
        // redb locks (required before the same dir can be reopened below).
        ProtocolHandler::shutdown(&docs).await;
        blobs.shutdown().await.unwrap();
        ep.close().await;
    }

    {
        let ep = Endpoint::builder(iroh::endpoint::presets::Minimal)
            .bind()
            .await
            .unwrap();
        let gossip = Gossip::builder().spawn(ep.clone());
        let blobs = FsStore::load(path.join("blobs")).await.unwrap();
        let docs = Docs::persistent(path.clone())
            .spawn(ep, (*blobs).clone(), gossip)
            .await
            .unwrap();

        // The persisted namespace must still be enumerable from the reopened store.
        let mut listed = docs.list().await.unwrap();
        let mut present = false;
        while let Some(item) = listed.next().await {
            let (id, _cap) = item.unwrap();
            if id == namespace {
                present = true;
            }
        }
        assert!(present, "namespace was not retained across reopen");

        // Reopen the same namespace as a writable Doc from the local persistent store.
        let doc = docs
            .open(namespace)
            .await
            .unwrap()
            .expect("namespace not found in reopened store");

        let stream = doc
            .get_many(Query::key_prefix(b"finding/".to_vec()))
            .await
            .unwrap();
        futures::pin_mut!(stream);
        let mut count = 0;
        while let Some(e) = stream.next().await {
            e.unwrap();
            count += 1;
        }
        assert_eq!(count, 1, "entry did not persist across reopen");
    }
}
