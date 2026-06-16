use serde_json::Value;
use std::collections::HashMap;
use tokio::sync::Mutex;

pub struct DocsStore {
    state: Mutex<HashMap<String, HashMap<String, Vec<(String, Value)>>>>,
}

impl DocsStore {
    pub fn new() -> Self {
        Self { state: Mutex::new(HashMap::new()) }
    }

    pub async fn open(&self, engagement_id: &str) -> anyhow::Result<()> {
        let mut state = self.state.lock().await;
        state.entry(engagement_id.to_string()).or_default();
        Ok(())
    }

    pub async fn insert(&self, engagement_id: &str, doc_type: &str, key: &str, value: Value) -> anyhow::Result<()> {
        let mut state = self.state.lock().await;
        let docs = state.entry(engagement_id.to_string()).or_default();
        let entries = docs.entry(doc_type.to_string()).or_default();
        entries.push((key.to_string(), value));
        Ok(())
    }

    pub async fn get_all(&self, engagement_id: &str, doc_type: &str) -> anyhow::Result<Vec<Value>> {
        let state = self.state.lock().await;
        let docs = state.get(engagement_id).and_then(|d| d.get(doc_type));
        Ok(docs.map(|entries| entries.iter().map(|(_, v)| v.clone()).collect()).unwrap_or_default())
    }
}
