use anyhow::Context;
use serde::Serialize;
use std::path::PathBuf;

#[derive(Debug, Serialize)]
pub struct IdentityInfo {
    pub node_id: String,
    pub public_key: String,
}

pub struct IdentityManager {
    secret_key: iroh::SecretKey,
}

impl IdentityManager {
    pub async fn load_or_create() -> anyhow::Result<Self> {
        let key_path = key_path();
        if key_path.exists() {
            let bytes = tokio::fs::read(&key_path)
                .await
                .context("Failed to read key file")?;
            let arr: [u8; 32] = bytes
                .try_into()
                .map_err(|_| anyhow::anyhow!("Invalid key file: expected 32 bytes"))?;
            let secret_key = iroh::SecretKey::from_bytes(&arr);
            Ok(Self { secret_key })
        } else {
            let secret_key = iroh::SecretKey::generate();
            let bytes = secret_key.to_bytes();
            tokio::fs::create_dir_all(key_path.parent().unwrap()).await?;
            tokio::fs::write(&key_path, bytes)
                .await
                .context("Failed to write key file")?;
            Ok(Self { secret_key })
        }
    }

    pub fn get_info(&self) -> anyhow::Result<IdentityInfo> {
        let public_key = self.secret_key.public();
        Ok(IdentityInfo {
            node_id: public_key.to_string(),
            public_key: public_key.to_string(),
        })
    }

    pub fn public_key(&self) -> iroh::PublicKey {
        self.secret_key.public()
    }

    pub fn node_id(&self) -> iroh::PublicKey {
        self.secret_key.public()
    }

    pub fn secret_key(&self) -> &iroh::SecretKey {
        &self.secret_key
    }
}

fn key_path() -> PathBuf {
    dirs::data_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("riftor-mesh")
        .join("identity.key")
}
