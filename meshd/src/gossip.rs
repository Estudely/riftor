use serde_json::Value;
use std::collections::HashMap;
use tokio::sync::Mutex;

pub struct GossipTopic {
    pub topic_id: String,
    messages: Mutex<Vec<Value>>,
}

pub struct GossipStore {
    topics: Mutex<HashMap<String, GossipTopic>>,
}

impl GossipStore {
    pub fn new() -> Self {
        Self { topics: Mutex::new(HashMap::new()) }
    }

    pub async fn join(&self, engagement_id: &str, subtopic: &str) -> anyhow::Result<()> {
        let topic_id = format!("riftor/{}/{}", engagement_id, subtopic);
        let mut topics = self.topics.lock().await;
        topics.insert(topic_id.clone(), GossipTopic { topic_id, messages: Mutex::new(Vec::new()) });
        Ok(())
    }

    pub async fn broadcast(&self, engagement_id: &str, subtopic: &str, message: Value) -> anyhow::Result<()> {
        let topic_id = format!("riftor/{}/{}", engagement_id, subtopic);
        let topics = self.topics.lock().await;
        if let Some(topic) = topics.get(&topic_id) {
            let mut msgs = topic.messages.lock().await;
            msgs.push(message);
        }
        Ok(())
    }

    pub async fn listen(&self, engagement_id: &str, subtopic: &str) -> anyhow::Result<Vec<Value>> {
        let topic_id = format!("riftor/{}/{}", engagement_id, subtopic);
        let topics = self.topics.lock().await;
        Ok(topics.get(&topic_id)
            .map(|t| t.messages.try_lock().map(|m| m.clone()).unwrap_or_default())
            .unwrap_or_default())
    }
}
