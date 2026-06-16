use crate::docs::DocsStore;
use crate::gossip::GossipStore;
use anyhow::Context;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use uuid::Uuid;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct EngagementMeta {
    pub id: String,
    pub name: String,
    pub created_at: String,
    pub node_id: String,
}

pub struct EngagementManager {
    docs: std::sync::Arc<DocsStore>,
    gossip: GossipStore,
    engagements: tokio::sync::Mutex<Vec<EngagementMeta>>,
    node_id: String,
}

impl EngagementManager {
    pub fn new(node_id: String, docs: std::sync::Arc<DocsStore>) -> Self {
        Self {
            docs,
            gossip: GossipStore::new(),
            engagements: tokio::sync::Mutex::new(Vec::new()),
            node_id,
        }
    }

    pub async fn create(&self, name: String) -> anyhow::Result<EngagementMeta> {
        let id = Uuid::new_v4().to_string();
        let meta = EngagementMeta {
            id: id.clone(),
            name,
            created_at: chrono::Utc::now().to_rfc3339(),
            node_id: self.node_id.clone(),
        };
        self.docs.open(&id).await?;
        self.gossip.join(&id, "submit").await?;
        self.gossip.join(&id, "activity").await?;
        self.gossip.join(&id, "presence").await?;
        self.gossip.join(&id, "processed").await?;
        let mut engagements = self.engagements.lock().await;
        engagements.push(meta.clone());
        Ok(meta)
    }

    pub async fn generate_invite(&self, engagement_id: &str) -> anyhow::Result<String> {
        let invite_id = Uuid::new_v4().to_string();
        let invite_data = json!({
            "engagement_id": engagement_id,
            "invite_id": invite_id,
            "created_at": chrono::Utc::now().to_rfc3339(),
        });
        let payload = serde_json::to_string(&invite_data)?;
        use base64::Engine;
        Ok(base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(payload.as_bytes()))
    }

    pub async fn join(&self, invite: &str) -> anyhow::Result<EngagementMeta> {
        use base64::Engine;
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(invite)
            .map_err(|e| anyhow::anyhow!("Invalid invite: {}", e))?;
        let invite_data: Value = serde_json::from_slice(&payload)?;
        let engagement_id = invite_data["engagement_id"]
            .as_str()
            .context("Invalid invite: missing engagement_id")?;
        self.docs.open(engagement_id).await?;
        self.gossip.join(engagement_id, "submit").await?;
        self.gossip.join(engagement_id, "activity").await?;
        self.gossip.join(engagement_id, "presence").await?;
        self.gossip.join(engagement_id, "processed").await?;
        let meta = EngagementMeta {
            id: engagement_id.to_string(),
            name: format!("Joined {}", engagement_id),
            created_at: chrono::Utc::now().to_rfc3339(),
            node_id: self.node_id.clone(),
        };
        let mut engagements = self.engagements.lock().await;
        engagements.push(meta.clone());
        Ok(meta)
    }

    pub async fn leave(&self, engagement_id: &str) -> anyhow::Result<()> {
        let mut engagements = self.engagements.lock().await;
        engagements.retain(|e| e.id != engagement_id);
        Ok(())
    }

    pub async fn submit(
        &self,
        engagement_id: &str,
        submission: &Value,
    ) -> anyhow::Result<String> {
        let submission_id = Uuid::new_v4().to_string();
        let entry = json!({
            "submission_id": submission_id,
            "submission": submission,
            "timestamp": chrono::Utc::now().to_rfc3339(),
        });
        self.gossip
            .broadcast(engagement_id, "submit", entry)
            .await?;
        Ok(submission_id)
    }

    pub async fn get_state(&self, engagement_id: &str) -> anyhow::Result<Value> {
        let findings: Vec<Value> = self.docs.get_all(engagement_id, "finding").await?;
        let hosts: Vec<Value> = self.docs.get_all(engagement_id, "host").await?;
        let services: Vec<Value> = self.docs.get_all(engagement_id, "service").await?;
        Ok(json!({ "findings": findings, "hosts": hosts, "services": services }))
    }
}
