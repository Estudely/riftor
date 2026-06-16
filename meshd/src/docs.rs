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

    pub async fn query_similar(
        &self,
        engagement_id: &str,
        target: &str,
        vuln_class: &str,
        limit: usize,
    ) -> anyhow::Result<Vec<Value>> {
        let state = self.state.lock().await;
        let docs = match state.get(engagement_id) {
            Some(d) => d,
            None => return Ok(Vec::new()),
        };
        let findings = match docs.get("finding") {
            Some(entries) => entries,
            None => return Ok(Vec::new()),
        };
        let mut candidates: Vec<Value> = findings
            .iter()
            .filter_map(|(_, v)| {
                let f_target = v.get("target").and_then(|t| t.as_str()).unwrap_or("");
                let f_class = v.get("vuln_class").and_then(|c| c.as_str()).unwrap_or("");
                if f_target == target || f_class == vuln_class {
                    Some(v.clone())
                } else {
                    None
                }
            })
            .collect();
        candidates.truncate(limit);
        Ok(candidates)
    }

    pub async fn get_all(&self, engagement_id: &str, doc_type: &str) -> anyhow::Result<Vec<Value>> {
        let state = self.state.lock().await;
        let docs = state.get(engagement_id).and_then(|d| d.get(doc_type));
        Ok(docs.map(|entries| entries.iter().map(|(_, v)| v.clone()).collect()).unwrap_or_default())
    }
}
