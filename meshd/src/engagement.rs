use crate::docs::DocsStore;
use crate::gossip::GossipStore;
use anyhow::Context;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use uuid::Uuid;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct EngagementMeta {
    pub id: String,
    pub name: String,
    pub created_at: String,
    pub node_id: String,
    /// P2P namespace identifier for this engagement.
    /// Currently a UUID; will transition to iroh-docs NamespaceId.
    pub namespace_id: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct InvitePayload {
    /// P2P namespace identifier
    namespace_id: String,
    /// NodeId of the inviter (to connect to for sync)
    node_id: String,
    /// Engagement UUID (for application-level identification)
    engagement_id: String,
    /// Relay URLs of the inviter node
    relay_urls: Vec<String>,
    /// Direct IP addresses of the inviter node
    direct_addresses: Vec<String>,
    /// iroh-docs read ticket for the engagement replica (None for legacy invites)
    #[serde(default)]
    doc_ticket: Option<String>,
    created_at: String,
}

pub struct EngagementManager {
    pub(crate) docs: Arc<DocsStore>,
    gossip: Arc<GossipStore>,
    engagements: tokio::sync::Mutex<Vec<EngagementMeta>>,
    node_id: String,
    relay_urls: Vec<String>,
    direct_addresses: Vec<String>,
    event_sink: Option<tokio::sync::mpsc::UnboundedSender<crate::protocol::Event>>,
}

impl EngagementManager {
    pub fn new(
        node_id: String,
        docs: Arc<DocsStore>,
        gossip: Arc<GossipStore>,
        endpoint: Arc<iroh::endpoint::Endpoint>,
    ) -> Self {
        let endpoint_addr = endpoint.addr();
        Self {
            docs,
            gossip,
            engagements: tokio::sync::Mutex::new(Vec::new()),
            node_id,
            relay_urls: endpoint_addr.relay_urls().map(|u| u.to_string()).collect(),
            direct_addresses: endpoint_addr.ip_addrs().map(|a| a.to_string()).collect(),
            event_sink: None,
        }
    }

    /// Register the sink that gossip-derived events are forwarded to. Optional:
    /// without a sink (tests/headless), receive loops still run but drop events.
    pub fn set_event_sink(
        &mut self,
        tx: tokio::sync::mpsc::UnboundedSender<crate::protocol::Event>,
    ) {
        self.event_sink = Some(tx);
    }

    /// Spawn a task that decodes incoming gossip messages on a topic and
    /// forwards them as `MeshEvent`s to the event sink (if any).
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

    /// Spawn a fire-and-forget task that broadcasts a `presence` heartbeat
    /// every 15s. Best-effort: broadcast errors are ignored and the task
    /// never panics or blocks engagement creation.
    fn spawn_presence(&self, engagement_id: String, node_id: String, gossip: Arc<GossipStore>) {
        tokio::spawn(async move {
            let mut tick = tokio::time::interval(std::time::Duration::from_secs(15));
            // Skip the immediate first tick so we don't broadcast before
            // settling; subsequent ticks fire every 15s.
            tick.tick().await;
            loop {
                tick.tick().await;
                let _ = gossip
                    .broadcast(
                        &engagement_id,
                        "presence",
                        json!({"node_id": node_id, "ts": chrono::Utc::now().to_rfc3339()}),
                    )
                    .await;
            }
        });
    }

    pub async fn create(&self, name: String) -> anyhow::Result<EngagementMeta> {
        let id = Uuid::new_v4().to_string();

        self.docs.open(&id).await?;
        let namespace_id = self
            .docs
            .namespace_id(&id)
            .await
            .map(|n| n.to_string())
            .unwrap_or_default();
        let bootstrap: Vec<iroh::EndpointId> = Vec::new();
        for sub in ["submit", "activity", "presence", "processed"] {
            let recv = self.gossip.join(&id, sub, bootstrap.clone()).await?;
            self.spawn_receive_loop(id.clone(), sub.to_string(), recv);
        }

        self.spawn_presence(id.clone(), self.node_id.clone(), self.gossip.clone());

        let meta = EngagementMeta {
            id: id.clone(),
            name,
            created_at: chrono::Utc::now().to_rfc3339(),
            node_id: self.node_id.clone(),
            namespace_id,
        };

        let mut engagements = self.engagements.lock().await;
        engagements.push(meta.clone());
        Ok(meta)
    }

    pub async fn generate_invite(&self, engagement_id: &str) -> anyhow::Result<String> {
        let engagements = self.engagements.lock().await;
        let meta = engagements
            .iter()
            .find(|e| e.id == engagement_id)
            .context("Engagement not found")?;

        let doc_ticket = self
            .docs
            .read_ticket(engagement_id)
            .await
            .ok()
            .map(|t| t.to_string());

        let invite = InvitePayload {
            namespace_id: meta.namespace_id.clone(),
            node_id: self.node_id.clone(),
            engagement_id: engagement_id.to_string(),
            relay_urls: self.relay_urls.clone(),
            direct_addresses: self.direct_addresses.clone(),
            doc_ticket,
            created_at: chrono::Utc::now().to_rfc3339(),
        };

        let payload = serde_json::to_string(&invite)?;
        use base64::Engine;
        Ok(base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(payload.as_bytes()))
    }

    pub async fn join(&self, invite_b64: &str) -> anyhow::Result<EngagementMeta> {
        use base64::Engine;
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .decode(invite_b64)
            .map_err(|e| anyhow::anyhow!("Invalid invite base64: {}", e))?;
        let invite: InvitePayload =
            serde_json::from_slice(&payload).context("Invalid invite payload")?;

        // Import the read ticket if present (adopts the inviter's namespace);
        // fall back to opening a local replica only for legacy invites.
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
            self.docs.open(&invite.engagement_id).await?;
        }

        // Join gossip topics, bootstrapping from the inviter's EndpointId.
        let bootstrap: Vec<iroh::EndpointId> = match invite.node_id.parse::<iroh::EndpointId>() {
            Ok(id) => vec![id],
            Err(e) => {
                tracing::warn!("bad inviter node_id '{}' in invite: {e}; bootstrapping empty", invite.node_id);
                Vec::new()
            }
        };
        for sub in ["submit", "activity", "presence", "processed"] {
            let recv = self
                .gossip
                .join(&invite.engagement_id, sub, bootstrap.clone())
                .await?;
            self.spawn_receive_loop(invite.engagement_id.clone(), sub.to_string(), recv);
        }

        self.spawn_presence(
            invite.engagement_id.clone(),
            self.node_id.clone(),
            self.gossip.clone(),
        );

        let meta = EngagementMeta {
            id: invite.engagement_id.clone(),
            name: format!("Joined {}", invite.engagement_id),
            created_at: chrono::Utc::now().to_rfc3339(),
            node_id: self.node_id.clone(),
            namespace_id: invite.namespace_id.clone(),
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
