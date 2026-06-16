use anyhow::Context;
use serde::Serialize;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
pub struct IdentityInfo {
    pub node_id: String,
    pub public_key: String,
}

pub struct IdentityManager {
    secret_key: String,
}

impl IdentityManager {
    pub async fn load_or_create() -> anyhow::Result<Self> {
        let key_path = key_path();
        if key_path.exists() {
            let bytes = tokio::fs::read(&key_path).await.context("Failed to read key file")?;
            let secret_key = String::from_utf8_lossy(&bytes).to_string();
            Ok(Self { secret_key })
        } else {
            let secret_key = uuid::Uuid::new_v4().to_string();
            tokio::fs::create_dir_all(key_path.parent().unwrap()).await?;
            tokio::fs::write(&key_path, secret_key.as_bytes()).await.context("Failed to write key file")?;
            Ok(Self { secret_key })
        }
    }

    pub fn get_info(&self) -> anyhow::Result<IdentityInfo> {
        Ok(IdentityInfo {
            node_id: self.secret_key[..12].to_string(),
            public_key: self.secret_key.clone(),
        })
    }

    pub fn public_key(&self) -> &str {
        &self.secret_key
    }
}

fn key_path() -> PathBuf {
    dirs::data_dir().unwrap_or_else(|| PathBuf::from("."))
        .join("riftor-mesh")
        .join("identity.key")
}
