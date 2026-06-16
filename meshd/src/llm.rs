use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::atomic::{AtomicU64, AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

#[derive(Debug, Clone)]
pub struct LlmConfig {
    pub api_key: String,
    pub api_base: String,
    pub model: String,
    pub temperature: f32,
}

impl Default for LlmConfig {
    fn default() -> Self {
        Self {
            api_key: std::env::var("DEEPSEEK_API_KEY")
                .or_else(|_| std::env::var("OPENAI_API_KEY"))
                .unwrap_or_default(),
            api_base: std::env::var("RIFTOR_API_BASE")
                .unwrap_or_else(|_| "https://api.deepseek.com/v1/chat/completions".into()),
            model: std::env::var("RIFTOR_MODEL")
                .unwrap_or_else(|_| "deepseek-chat".into()),
            temperature: std::env::var("RIFTOR_TEMPERATURE")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(0.3),
        }
    }
}

#[derive(Debug, Serialize)]
struct LlmRequest {
    model: String,
    messages: Vec<LlmMessage>,
    temperature: f32,
    max_tokens: u32,
}

#[derive(Debug, Serialize)]
struct LlmMessage {
    role: String,
    content: String,
}

#[derive(Debug, Deserialize)]
struct LlmResponse {
    choices: Vec<LlmChoice>,
}

#[derive(Debug, Deserialize)]
struct LlmChoice {
    message: LlmContentBlock,
}

#[derive(Debug, Deserialize)]
struct LlmContentBlock {
    content: String,
}

#[derive(Debug)]
pub struct CircuitBreaker {
    failure_count: AtomicU64,
    last_failure: Arc<Mutex<Option<Instant>>>,
    open: AtomicBool,
    cooldown_secs: u64,
}

impl Clone for CircuitBreaker {
    fn clone(&self) -> Self {
        Self {
            failure_count: AtomicU64::new(self.failure_count.load(Ordering::Relaxed)),
            last_failure: Arc::new(Mutex::new(*self.last_failure.blocking_lock())),
            open: AtomicBool::new(self.open.load(Ordering::Relaxed)),
            cooldown_secs: self.cooldown_secs,
        }
    }
}

impl CircuitBreaker {
    pub fn new(cooldown_secs: u64) -> Self {
        Self {
            failure_count: AtomicU64::new(0),
            last_failure: Arc::new(Mutex::new(None)),
            open: AtomicBool::new(false),
            cooldown_secs,
        }
    }

    pub fn is_open(&self) -> bool {
        if !self.open.load(Ordering::Relaxed) {
            return false;
        }
        let last = self.last_failure.blocking_lock();
        if let Some(instant) = *last {
            if instant.elapsed() >= Duration::from_secs(self.cooldown_secs) {
                self.open.store(false, Ordering::Relaxed);
                self.failure_count.store(0, Ordering::Relaxed);
                return false;
            }
        }
        true
    }

    pub fn record_failure(&self) {
        let prev = self.failure_count.fetch_add(1, Ordering::Relaxed);
        if prev + 1 >= 5 {
            self.open.store(true, Ordering::Relaxed);
            let mut last = self.last_failure.blocking_lock();
            *last = Some(Instant::now());
        }
    }

    pub fn record_success(&self) {
        self.failure_count.store(0, Ordering::Relaxed);
        self.open.store(false, Ordering::Relaxed);
    }
}

pub struct LlmClient {
    config: LlmConfig,
    client: reqwest::Client,
    pub circuit_breaker: CircuitBreaker,
}

impl LlmClient {
    pub fn new(config: LlmConfig) -> Self {
        Self {
            config,
            client: reqwest::Client::new(),
            circuit_breaker: CircuitBreaker::new(60),
        }
    }

    pub async fn complete(
        &self,
        system_prompt: &str,
        user_message: &str,
    ) -> anyhow::Result<Value> {
        if self.circuit_breaker.is_open() {
            anyhow::bail!("Circuit breaker open - LLM calls suspended");
        }

        let request = LlmRequest {
            model: self.config.model.clone(),
            messages: vec![
                LlmMessage {
                    role: "system".into(),
                    content: system_prompt.to_string(),
                },
                LlmMessage {
                    role: "user".into(),
                    content: user_message.to_string(),
                },
            ],
            temperature: self.config.temperature,
            max_tokens: 1024,
        };

        let mut last_err: Option<anyhow::Error> = None;

        for attempt in 0..3 {
            if attempt > 0 {
                let delay = Duration::from_millis(1000 * 2u64.pow(attempt));
                tokio::time::sleep(delay).await;
            }

            match self.try_call(&request).await {
                Ok(value) => {
                    self.circuit_breaker.record_success();
                    return Ok(value);
                }
                Err(e) => {
                    last_err = Some(e);
                }
            }
        }

        self.circuit_breaker.record_failure();
        Err(last_err.unwrap_or_else(|| anyhow::anyhow!("LLM call failed after 3 retries")))
    }

    async fn try_call(&self, request: &LlmRequest) -> anyhow::Result<Value> {
        let resp = self
            .client
            .post(&self.config.api_base)
            .header("Authorization", format!("Bearer {}", &self.config.api_key))
            .header("Content-Type", "application/json")
            .json(request)
            .timeout(Duration::from_secs(30))
            .send()
            .await?;

        if !resp.status().is_success() {
            let status = resp.status();
            let body = resp.text().await.unwrap_or_default();
            anyhow::bail!("LLM API error {}: {}", status, body);
        }

        let llm_resp: LlmResponse = resp.json().await?;
        let text = llm_resp
            .choices
            .first()
            .map(|c| c.message.content.clone())
            .unwrap_or_default();

        let json_str = text
            .trim()
            .trim_start_matches("```json")
            .trim_start_matches("```")
            .trim_end_matches("```")
            .trim();

        let value: Value = serde_json::from_str(json_str)?;
        Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_llm_config_defaults() {
        let config = LlmConfig::default();
        assert!(!config.model.is_empty());
        assert!(!config.api_base.is_empty());
    }

    #[test]
    fn test_circuit_breaker_opens_after_failures() {
        let cb = CircuitBreaker::new(60);
        assert!(!cb.is_open());
        for _ in 0..5 {
            cb.record_failure();
        }
        assert!(cb.is_open());
    }

    #[test]
    fn test_circuit_breaker_resets_on_success() {
        let cb = CircuitBreaker::new(60);
        for _ in 0..4 {
            cb.record_failure();
        }
        assert!(!cb.is_open());
        cb.record_success();
        assert_eq!(cb.failure_count.load(Ordering::Relaxed), 0);
    }
}
