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
        let blobs = tokio::time::timeout(
            std::time::Duration::from_secs(5),
            FsStore::load(dir.join("blobs")),
        )
        .await
        .map_err(|_| anyhow::anyhow!("blobs store open timed out (is another daemon running?)"))??;
        let blobs_api = Arc::new((*blobs).clone());
        let docs = Docs::persistent(dir.clone())
            .spawn(endpoint.clone(), (*blobs).clone(), gossip.clone())
            .await?;
        Ok((docs, blobs_api))
    }
}
