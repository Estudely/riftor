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
        let stream = doc.get_many(Query::key_prefix(prefix.as_bytes())).await?;
        futures::pin_mut!(stream);
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
        use iroh_docs::api::protocol::{AddrInfoOptions, ShareMode};
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
}
