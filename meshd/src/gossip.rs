use anyhow::Context;
use bytes::Bytes;
use iroh::EndpointId;
use iroh_gossip::api::{GossipReceiver, GossipSender};
use iroh_gossip::net::Gossip;
use iroh_gossip::proto::TopicId;
use serde_json::Value;
use std::collections::HashMap;
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
        bootstrap: Vec<EndpointId>,
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
    use iroh::address_lookup::memory::MemoryLookup;
    use iroh::endpoint::Endpoint;
    use iroh_gossip::api::Event;
    use serde_json::json;
    use std::time::Duration;

    async fn node() -> (GossipStore, Endpoint, MemoryLookup, iroh::protocol::Router) {
        let lookup = MemoryLookup::new();
        let ep = Endpoint::builder(iroh::endpoint::presets::Minimal)
            .address_lookup(lookup.clone())
            .bind()
            .await
            .unwrap();
        let gossip = Gossip::builder().spawn(ep.clone());
        let router = iroh::protocol::Router::builder(ep.clone())
            .accept(iroh_gossip::net::GOSSIP_ALPN, gossip.clone())
            .spawn();
        (GossipStore::new(gossip), ep, lookup, router)
    }

    #[tokio::test]
    async fn broadcast_reaches_subscriber() {
        let (a, a_ep, a_lookup, _a_router) = node().await;
        let (b, b_ep, b_lookup, _b_router) = node().await;

        let a_id = a_ep.id();
        let b_id = b_ep.id();

        // Under the `Minimal` preset there is no relay and no DNS lookup, so a bare
        // EndpointId is not dialable. Teach each endpoint the other's local address
        // out-of-band via MemoryLookup before forming the gossip overlay.
        a_lookup.add_endpoint_info(b_ep.addr());
        b_lookup.add_endpoint_info(a_ep.addr());

        let _recv_a = a.join("eng1", "activity", vec![b_id]).await.unwrap();
        let mut recv_b = b.join("eng1", "activity", vec![a_id]).await.unwrap();

        // Allow the overlay to form, then re-broadcast inside the poll loop in case
        // the first send races neighbor establishment.
        tokio::time::sleep(Duration::from_millis(1000)).await;

        let mut got = None;
        for i in 0..50 {
            if i % 5 == 0 {
                a.broadcast("eng1", "activity", json!({"msg": "hello"}))
                    .await
                    .unwrap();
            }
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
    }
}
