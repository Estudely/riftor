use std::collections::HashMap;
use tokio::sync::Mutex;

static BLOB_STORE: std::sync::LazyLock<Mutex<HashMap<String, Vec<u8>>>> =
    std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

pub async fn add_blob(_engagement_id: &str, data: &[u8]) -> anyhow::Result<String> {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    let mut hasher = DefaultHasher::new();
    data.hash(&mut hasher);
    let hash = format!("{:x}", hasher.finish());
    let mut store = BLOB_STORE.lock().await;
    store.insert(hash.clone(), data.to_vec());
    Ok(hash)
}

pub async fn get_blob(_engagement_id: &str, hash: &str) -> anyhow::Result<Vec<u8>> {
    let store = BLOB_STORE.lock().await;
    store.get(hash).cloned().ok_or_else(|| anyhow::anyhow!("Blob not found: {}", hash))
}

pub async fn blob_exists(_engagement_id: &str, hash: &str) -> bool {
    let store = BLOB_STORE.lock().await;
    store.contains_key(hash)
}
