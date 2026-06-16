use serde_json::Value;
use std::collections::HashMap;
use tokio::sync::Mutex;

pub struct GossipStore {
    topics: Mutex<HashMap<String, Vec<Value>>>,
}

impl GossipStore {
    pub fn new() -> Self {
        Self {
            topics: Mutex::new(HashMap::new()),
        }
    }

    pub async fn join(&self, engagement_id: &str, subtopic: &str) -> anyhow::Result<()> {
        let topic_id = format!("riftor/{}/{}", engagement_id, subtopic);
        let mut topics = self.topics.lock().await;
        topics.entry(topic_id).or_default();
        Ok(())
    }

    pub async fn broadcast(
        &self,
        engagement_id: &str,
        subtopic: &str,
        message: Value,
    ) -> anyhow::Result<()> {
        let topic_id = format!("riftor/{}/{}", engagement_id, subtopic);
        let mut topics = self.topics.lock().await;
        if let Some(msgs) = topics.get_mut(&topic_id) {
            msgs.push(message);
        }
        Ok(())
    }

    pub async fn listen(
        &self,
        engagement_id: &str,
        subtopic: &str,
    ) -> anyhow::Result<Vec<Value>> {
        let topic_id = format!("riftor/{}/{}", engagement_id, subtopic);
        let topics = self.topics.lock().await;
        Ok(topics.get(&topic_id).cloned().unwrap_or_default())
    }
}
