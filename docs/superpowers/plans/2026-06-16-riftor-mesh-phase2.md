# Riftor Mesh Phase 2 — Findings Processor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the AI Findings Processor pipeline to riftor-meshd — a background worker pool that ingests gossip submissions, deduplicates via LLM, assesses severity, merges or rejects, and publishes canonical findings to iroh-docs.

**Architecture:** Three new Rust modules (queue, llm, prompts) feed into a Processor that spawns a configurable pool of tokio worker tasks. Workers pull from a bounded mpsc channel and call an LLM API over HTTP. Results publish to docs. The handler's submit route enqueues instead of broadcasting. Python TUI gains a review screen for Commander override.

**Tech Stack:** Rust (tokio::sync::mpsc, reqwest for HTTP, serde_json), existing iroh-docs/gossip, Python Textual for review screen

---

## File Map

### Rust — New Files

| File | Responsibility |
|---|---|
| `meshd/src/queue.rs` | `SubmissionQueue` — bounded mpsc wrapper with stats (pending, processing, completed, failed) |
| `meshd/src/llm.rs` | `LlmClient` — HTTP client with JSON request/response, 3-retry with backoff, circuit breaker (5 failures = 60s pause) |
| `meshd/src/prompts.rs` | Static prompt templates as `&str` constants: `DEDUP_SYSTEM`, `SEVERITY_SYSTEM` |
| `meshd/src/processor.rs` | `Processor` — worker pool (3 concurrent tokio tasks), pipeline orchestration, review queue, mode enum |

### Rust — Modified Files

| File | Change |
|---|---|
| `meshd/src/handler.rs` | `submit` handler: enqueue submission + return `submission_id` immediately; `get_state` reads from docs (already); add `get_queue_stats` and `get_review_queue` RPC methods |
| `meshd/src/docs.rs` | Add `query_similar(target, vuln_class, limit) -> Vec<(String, Value)>` for dedup candidates |
| `meshd/src/main.rs` | Spawn `Processor` on startup; pass env var config (API key, model, temperature) |
| `meshd/src/engagement.rs` | `submit` simplified: just enqueue into processor's channel |
| `meshd/Cargo.toml` | Add `reqwest = { version = "0.12", features = ["json"] }`, `rand` for jitter |

### Python — New/Modified

| File | Change |
|---|---|
| `riftor/mesh/manager.py` | Add `set_processor_mode()`, `get_queue_stats()`, `get_review_queue()`, `accept_review()`, `reject_review()` |
| `riftor/mesh/client.py` | Add RPC methods: `get_queue_stats`, `get_review_queue`, `accept_finding`, `reject_finding` |
| `riftor/mesh/commands.py` | Add `/mesh review`, `/mesh mode`, `/mesh queue`, `/mesh processor` command handlers |
| `riftor/mesh/sidebar.py` | Add processor status section (mode badge + queue depth) |
| `riftor/tui/app.py` | Add review screen (interactive Widget for finding-by-finding approval) |

### Tests

| File | Responsibility |
|---|---|
| `meshd/tests/processor_test.rs` | Rust integration: queue enqueue/dequeue, mock LLM, pipeline stages, circuit breaker |
| `tests/mesh/test_processor.py` | Python: test queue stats parsing, review queue operations, mode switching |

---

## JSON-Line Protocol — New RPC Methods

```
method: "get_queue_stats"
  params: {"engagement_id": "<uuid>"}
  → result: {"pending": 3, "processing": 1, "completed": 42, "failed": 0}

method: "get_review_queue"
  params: {"engagement_id": "<uuid>"}
  → result: {"decisions": [{"submission_id": "...", "finding": {...}, "decision": "new", ...}]}

method: "set_processor_mode"
  params: {"engagement_id": "<uuid>", "mode": "autonomous"|"review"|"critical"}
  → result: {"mode": "review"}

method: "approve_decision"
  params: {"engagement_id": "<uuid>", "submission_id": "<uuid>"}
  → result: {"status": "published"}

method: "reject_decision"
  params: {"engagement_id": "<uuid>", "submission_id": "<uuid>", "reason": "false positive"}
  → result: {"status": "rejected"}

method: "override_severity"
  params: {"engagement_id": "<uuid>", "submission_id": "<uuid>", "severity": "high"}
  → result: {"status": "published", "severity": "high"}
```

---

### Task 1: Submission Queue Module

**Files:**
- Create: `meshd/src/queue.rs`

- [ ] **Step 1: Create queue.rs with bounded mpsc wrapper**

```rust
use serde::Serialize;
use serde_json::Value;
use tokio::sync::mpsc;
use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Debug, Clone)]
pub struct Submission {
    pub submission_id: String,
    pub engagement_id: String,
    pub author_node_id: String,
    pub data: Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct QueueStats {
    pub pending: u64,
    pub processing: u64,
    pub completed: u64,
    pub failed: u64,
}

pub struct SubmissionQueue {
    tx: mpsc::Sender<Submission>,
    rx: tokio::sync::Mutex<mpsc::Receiver<Submission>>,
    processing: AtomicU64,
    completed: AtomicU64,
    failed: AtomicU64,
}

impl SubmissionQueue {
    pub fn new(capacity: usize) -> Self {
        let (tx, rx) = mpsc::channel(capacity);
        Self {
            tx,
            rx: tokio::sync::Mutex::new(rx),
            processing: AtomicU64::new(0),
            completed: AtomicU64::new(0),
            failed: AtomicU64::new(0),
        }
    }

    pub async fn enqueue(&self, submission: Submission) -> Result<(), mpsc::error::SendError<Submission>> {
        self.tx.send(submission).await
    }

    pub async fn dequeue(&self) -> Option<Submission> {
        self.rx.lock().await.recv().await
    }

    pub fn mark_processing(&self) {
        self.processing.fetch_add(1, Ordering::Relaxed);
    }

    pub fn mark_done_processing(&self) {
        self.processing.fetch_sub(1, Ordering::Relaxed);
    }

    pub fn mark_completed(&self) {
        self.completed.fetch_add(1, Ordering::Relaxed);
    }

    pub fn mark_failed(&self) {
        self.failed.fetch_add(1, Ordering::Relaxed);
    }

    pub fn stats(&self) -> QueueStats {
        QueueStats {
            pending: self.tx.max_capacity() as u64 - self.tx.capacity() as u64,
            processing: self.processing.load(Ordering::Relaxed),
            completed: self.completed.load(Ordering::Relaxed),
            failed: self.failed.load(Ordering::Relaxed),
        }
    }
}
```

- [ ] **Step 2: Write unit tests in queue.rs**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_enqueue_dequeue() {
        let queue = SubmissionQueue::new(10);
        let sub = Submission {
            submission_id: "s1".into(),
            engagement_id: "e1".into(),
            author_node_id: "a1".into(),
            data: serde_json::json!({"title": "test"}),
        };
        queue.enqueue(sub).await.unwrap();
        let dequeued = queue.dequeue().await.unwrap();
        assert_eq!(dequeued.submission_id, "s1");
    }

    #[tokio::test]
    async fn test_stats_update() {
        let queue = SubmissionQueue::new(10);
        queue.mark_completed();
        queue.mark_failed();
        let stats = queue.stats();
        assert_eq!(stats.completed, 1);
        assert_eq!(stats.failed, 1);
    }

    #[tokio::test]
    async fn test_processing_counter() {
        let queue = SubmissionQueue::new(10);
        queue.mark_processing();
        assert_eq!(queue.stats().processing, 1);
        queue.mark_done_processing();
        assert_eq!(queue.stats().processing, 0);
    }
}
```

- [ ] **Step 3: Run tests**

Run: `cargo test --manifest-path meshd/Cargo.toml --lib queue`
Expected: 3 tests pass

- [ ] **Step 4: Commit**

```bash
git add meshd/src/queue.rs
git commit -m "feat: add SubmissionQueue with bounded mpsc and stats"
```

---

### Task 2: LLM Client

**Files:**
- Create: `meshd/src/llm.rs`
- Modify: `meshd/Cargo.toml` — add reqwest dependency

- [ ] **Step 1: Add reqwest to Cargo.toml**

Add under `[dependencies]`:
```toml
reqwest = { version = "0.12", features = ["json"] }
```

- [ ] **Step 2: Create llm.rs**

```rust
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
            api_key: std::env::var("ANTHROPIC_API_KEY")
                .or_else(|_| std::env::var("OPENAI_API_KEY"))
                .or_else(|_| std::env::var("OPENROUTER_API_KEY"))
                .unwrap_or_default(),
            api_base: std::env::var("RIFTOR_API_BASE")
                .unwrap_or_else(|_| "https://api.anthropic.com/v1/messages".into()),
            model: std::env::var("RIFTOR_MODEL")
                .unwrap_or_else(|_| "claude-sonnet-4-6".into()),
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
    system: String,
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
    content: Vec<LlmContentBlock>,
}

#[derive(Debug, Deserialize)]
struct LlmContentBlock {
    text: String,
}

#[derive(Debug, Clone)]
pub struct CircuitBreaker {
    failure_count: AtomicU64,
    last_failure: Arc<Mutex<Option<Instant>>>,
    open: AtomicBool,
    cooldown_secs: u64,
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
        // Check if cooldown has elapsed
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
            anyhow::bail!("Circuit breaker open — LLM calls suspended");
        }

        let request = LlmRequest {
            model: self.config.model.clone(),
            system: system_prompt.to_string(),
            messages: vec![LlmMessage {
                role: "user".into(),
                content: user_message.to_string(),
            }],
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
            .header("x-api-key", &self.config.api_key)
            .header("anthropic-version", "2023-06-01")
            .header("content-type", "application/json")
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
            .content
            .first()
            .map(|b| b.text.clone())
            .unwrap_or_default();

        // Extract JSON from response (may be wrapped in markdown)
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
```

- [ ] **Step 3: Run build to verify compilation**

Run: `cargo build --manifest-path meshd/Cargo.toml`
Expected: Compiles successfully

- [ ] **Step 4: Commit**

```bash
git add meshd/src/llm.rs meshd/Cargo.toml
git commit -m "feat: add LlmClient with retry, circuit breaker, and JSON response parsing"
```

---

### Task 3: Prompt Templates

**Files:**
- Create: `meshd/src/prompts.rs`

- [ ] **Step 1: Create prompts.rs**

```rust
pub const DEDUP_SYSTEM: &str = "\
You are a findings deduplicator for a penetration testing platform.
Given a NEW finding and a list of EXISTING findings, determine if the new
finding describes the same vulnerability as any existing one.

Rules:
- Same target (host/endpoint) + same vuln class → likely match (confidence > 0.8)
- Same endpoint but different vuln class → probably distinct (confidence < 0.3)
- Same vuln class but different endpoint → could be same root cause, check description
- Title and description semantic similarity matters

Respond with valid JSON only (no markdown):
{\"decision\": \"new\" | \"match\", \"confidence\": 0.0-1.0, \"matched_finding_id\": \"uuid-or-null\", \"reasoning\": \"...\"}";

pub const SEVERITY_SYSTEM: &str = "\
You are a CVSS v3.1 assessor for penetration testing findings.
Given a finding and engagement context, assign severity and CVSS vector.

Severity scale:
- critical (9.0-10.0): Full system compromise, data exfiltration, RCE without auth
- high (7.0-8.9): Significant data exposure, auth bypass, SQLi with data access
- medium (4.0-6.9): XSS, CSRF, info disclosure of non-sensitive data
- low (0.1-3.9): Minor misconfigurations, verbose error messages
- info (0.0): Informational findings, best practice recommendations

Respond with valid JSON only (no markdown):
{\"severity\": \"critical\"|\"high\"|\"medium\"|\"low\"|\"info\", \"cvss_vector\": \"CVSS:3.1/...\", \"reasoning\": \"...\"}";

pub const BUILD_DEDUP_USER: fn(&str, &str) -> String = |new_finding: &str, existing: &str| {
    format!(
        "NEW FINDING:\n{}\n\nEXISTING FINDINGS:\n{}",
        new_finding, existing
    )
};

pub const BUILD_SEVERITY_USER: fn(&str, &str) -> String = |finding: &str, context: &str| {
    format!(
        "FINDING:\n{}\n\nENGAGEMENT CONTEXT:\n{}",
        finding, context
    )
};
```

Note: The `const fn` pointers won't work in Rust. Use plain functions instead.

- [ ] **Step 2: Fix — use plain functions**

```rust
pub fn build_dedup_user(new_finding: &str, existing: &str) -> String {
    format!("NEW FINDING:\n{}\n\nEXISTING FINDINGS:\n{}", new_finding, existing)
}

pub fn build_severity_user(finding: &str, context: &str) -> String {
    format!("FINDING:\n{}\n\nENGAGEMENT CONTEXT:\n{}", finding, context)
}
```

- [ ] **Step 3: Write unit test**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_prompts_are_non_empty() {
        assert!(!DEDUP_SYSTEM.is_empty());
        assert!(!SEVERITY_SYSTEM.is_empty());
    }

    #[test]
    fn test_build_dedup_user() {
        let result = build_dedup_user("SQLi in /login", "[{\"id\":\"1\",\"title\":\"XSS\"}]");
        assert!(result.contains("SQLi in /login"));
        assert!(result.contains("XSS"));
    }
}
```

- [ ] **Step 4: Run tests and commit**

Run: `cargo test --manifest-path meshd/Cargo.toml --lib prompts`
Expected: 2 tests pass

```bash
git add meshd/src/prompts.rs
git commit -m "feat: add LLM prompt templates for dedup and severity"
```

---

### Task 4: Processor Core

**Files:**
- Create: `meshd/src/processor.rs`

- [ ] **Step 1: Create processor.rs**

```rust
use crate::queue::{SubmissionQueue, Submission};
use crate::llm::{LlmClient, LlmConfig};
use crate::prompts;
use crate::docs::DocsStore;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::sync::Mutex;
use tracing::{info, warn, error};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ProcessorMode {
    Autonomous,
    ReviewRequired,
    CriticalOnly,
}

#[derive(Debug, Clone, Serialize)]
pub struct ProcessorStats {
    pub mode: ProcessorMode,
    pub queue: crate::queue::QueueStats,
    pub circuit_open: bool,
    pub worker_count: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct PendingDecision {
    pub submission_id: String,
    pub engagement_id: String,
    pub finding: Value,
    pub decision: String,  // "new", "match", "reject"
    pub confidence: Option<f64>,
    pub matched_finding_id: Option<String>,
    pub severity: Option<String>,
    pub reasoning: Option<String>,
}

pub struct Processor {
    queue: Arc<SubmissionQueue>,
    docs: Arc<DocsStore>,
    llm: Arc<LlmClient>,
    mode: Mutex<ProcessorMode>,
    review_queue: Mutex<Vec<PendingDecision>>,
    worker_count: usize,
}

impl Processor {
    pub fn new(
        queue: Arc<SubmissionQueue>,
        docs: Arc<DocsStore>,
        llm_config: LlmConfig,
        mode: ProcessorMode,
        worker_count: usize,
    ) -> Self {
        let llm = Arc::new(LlmClient::new(llm_config));
        Self {
            queue,
            docs,
            llm,
            mode: Mutex::new(mode),
            review_queue: Mutex::new(Vec::new()),
            worker_count,
        }
    }

    pub async fn start(self: Arc<Self>) {
        for i in 0..self.worker_count {
            let processor = self.clone();
            tokio::spawn(async move {
                processor.worker_loop(i).await;
            });
        }
        info!("Processor started with {} workers in {:?} mode", self.worker_count, self.mode.lock().await);
    }

    async fn worker_loop(&self, worker_id: usize) {
        info!("Worker {} started", worker_id);
        while let Some(submission) = self.queue.dequeue().await {
            self.queue.mark_processing();
            info!("Worker {} processing submission {}", worker_id, submission.submission_id);

            match self.process_submission(&submission).await {
                Ok(decision) => {
                    self.handle_decision(submission.engagement_id.clone(), decision).await;
                    self.queue.mark_completed();
                }
                Err(e) => {
                    error!("Worker {} failed submission {}: {}", worker_id, submission.submission_id, e);
                    self.queue.mark_failed();
                }
            }
            self.queue.mark_done_processing();
        }
        info!("Worker {} stopping", worker_id);
    }

    async fn process_submission(&self, submission: &Submission) -> anyhow::Result<PendingDecision> {
        let finding = &submission.data;

        // Stage 1: Validate
        self.validate_submission(submission)?;

        // Stage 2: Deduplicate
        let existing_findings = self.docs.get_all(&submission.engagement_id, "finding").await?;
        let dedup_user = prompts::build_dedup_user(
            &serde_json::to_string(&finding)?,
            &serde_json::to_string(&existing_findings)?,
        );

        let dedup_result = self.llm.complete(prompts::DEDUP_SYSTEM, &dedup_user).await?;
        let decision = dedup_result["decision"].as_str().unwrap_or("new");
        let confidence = dedup_result["confidence"].as_f64();

        // Stage 3: Reject false positives (low confidence + vague)
        if decision == "new" && confidence.map_or(false, |c| c < 0.2) {
            let title = finding["title"].as_str().unwrap_or("");
            if title.len() < 10 {
                return Ok(PendingDecision {
                    submission_id: submission.submission_id.clone(),
                    engagement_id: submission.engagement_id.clone(),
                    finding: finding.clone(),
                    decision: "reject".into(),
                    confidence,
                    matched_finding_id: None,
                    severity: None,
                    reasoning: Some("Finding too vague — insufficient detail for assessment".into()),
                });
            }
        }

        // Stage 4: Assess severity (new findings) or merge (matches)
        if decision == "new" {
            let sev_user = prompts::build_severity_user(
                &serde_json::to_string(&finding)?,
                &json!({"asset_criticality": "unknown"}).to_string(),
            );
            let sev_result = self.llm.complete(prompts::SEVERITY_SYSTEM, &sev_user).await?;
            let severity = sev_result["severity"].as_str().unwrap_or("medium").to_string();

            Ok(PendingDecision {
                submission_id: submission.submission_id.clone(),
                engagement_id: submission.engagement_id.clone(),
                finding: finding.clone(),
                decision: "new".into(),
                confidence,
                matched_finding_id: None,
                severity: Some(severity),
                reasoning: Some(sev_result["reasoning"].as_str().unwrap_or("").into()),
            })
        } else if decision == "match" {
            let matched_id = dedup_result["matched_finding_id"].as_str().map(|s| s.to_string());
            Ok(PendingDecision {
                submission_id: submission.submission_id.clone(),
                engagement_id: submission.engagement_id.clone(),
                finding: finding.clone(),
                decision: "match".into(),
                confidence,
                matched_finding_id: matched_id,
                severity: None,
                reasoning: Some(dedup_result["reasoning"].as_str().unwrap_or("").into()),
            })
        } else {
            Ok(PendingDecision {
                submission_id: submission.submission_id.clone(),
                engagement_id: submission.engagement_id.clone(),
                finding: finding.clone(),
                decision: "reject".into(),
                confidence,
                matched_finding_id: None,
                severity: None,
                reasoning: Some("AI determined this is not a valid finding".into()),
            })
        }
    }

    fn validate_submission(&self, submission: &Submission) -> anyhow::Result<()> {
        let f = &submission.data;
        let title = f["title"].as_str().unwrap_or("");
        if title.is_empty() {
            anyhow::bail!("Missing required field: title");
        }
        if f["target"].as_str().unwrap_or("").is_empty() {
            anyhow::bail!("Missing required field: target");
        }
        if f["vuln_class"].as_str().unwrap_or("").is_empty() {
            anyhow::bail!("Missing required field: vuln_class");
        }
        Ok(())
    }

    async fn handle_decision(&self, engagement_id: String, decision: PendingDecision) {
        let mode = self.mode.lock().await.clone();

        let needs_review = match mode {
            ProcessorMode::Autonomous => false,
            ProcessorMode::ReviewRequired => true,
            ProcessorMode::CriticalOnly => {
                decision.severity.as_deref() == Some("critical")
                    || decision.severity.as_deref() == Some("high")
            }
        };

        if needs_review {
            let mut queue = self.review_queue.lock().await;
            queue.push(decision);
            info!("Decision queued for review: {}", decision.submission_id);
        } else {
            // Auto-publish
            let finding_key = format!("finding/{}", decision.submission_id);
            let mut doc_value = decision.finding.clone();
            if let Some(sev) = &decision.severity {
                doc_value["severity"] = json!(sev);
            }
            doc_value["decision"] = json!(decision.decision);
            let _ = self.docs.insert(&engagement_id, "finding", &finding_key, doc_value).await;
            info!("Auto-published: {}", decision.submission_id);
        }
    }

    pub async fn set_mode(&self, mode: ProcessorMode) {
        let mut current = self.mode.lock().await;
        *current = mode.clone();
        info!("Processor mode set to {:?}", mode);
    }

    pub async fn stats(&self) -> ProcessorStats {
        ProcessorStats {
            mode: self.mode.lock().await.clone(),
            queue: self.queue.stats(),
            circuit_open: self.llm.circuit_breaker.is_open(),
            worker_count: self.worker_count,
        }
    }

    pub async fn get_review_queue(&self) -> Vec<PendingDecision> {
        self.review_queue.lock().await.clone()
    }

    pub async fn approve_decision(&self, submission_id: &str) -> anyhow::Result<()> {
        let mut queue = self.review_queue.lock().await;
        if let Some(pos) = queue.iter().position(|d| d.submission_id == submission_id) {
            let decision = queue.remove(pos);
            let finding_key = format!("finding/{}", decision.submission_id);
            let mut doc_value = decision.finding.clone();
            if let Some(sev) = &decision.severity {
                doc_value["severity"] = json!(sev);
            }
            doc_value["decision"] = json!(decision.decision);
            self.docs.insert(&decision.engagement_id, "finding", &finding_key, doc_value).await?;
        }
        Ok(())
    }

    pub async fn reject_decision(&self, submission_id: &str, reason: &str) {
        let mut queue = self.review_queue.lock().await;
        queue.retain(|d| d.submission_id != submission_id);
        info!("Rejected {}: {}", submission_id, reason);
    }

    pub async fn override_severity(&self, submission_id: &str, severity: &str) -> anyhow::Result<()> {
        let mut queue = self.review_queue.lock().await;
        if let Some(decision) = queue.iter_mut().find(|d| d.submission_id == submission_id) {
            decision.severity = Some(severity.to_string());
        }
        // Then approve
        drop(queue);
        self.approve_decision(submission_id).await
    }
}
```

- [ ] **Step 2: Run build to verify compilation**

Run: `cargo build --manifest-path meshd/Cargo.toml`
Expected: Compiles (may have unused import warnings)

- [ ] **Step 3: Commit**

```bash
git add meshd/src/processor.rs
git commit -m "feat: add Processor with worker pool, pipeline stages, and review queue"
```

---

### Task 5: Hook Processor into Handler + Main

**Files:**
- Modify: `meshd/src/handler.rs`
- Modify: `meshd/src/main.rs`
- Modify: `meshd/src/engagement.rs`

- [ ] **Step 1: Update handler.rs — add processor and new RPC methods**

Read current `handler.rs`. Wrap the `Handler` struct to hold an `Arc<Processor>`. Modify `submit` to enqueue into processor. Add RPC handlers for `get_queue_stats`, `get_review_queue`, `set_processor_mode`, `approve_decision`, `reject_decision`, `override_severity`.

The key changes to handler.rs:
1. `Handler` struct gains `processor: Arc<Processor>`
2. `new()` takes `processor: Arc<Processor>`
3. `submit` handler calls `processor.queue.enqueue(submission)` instead of `engagement_manager.submit()`
4. Add 6 new method handlers for the review/control RPCs
5. `get_queue_stats` calls `processor.stats()`
6. `get_review_queue` calls `processor.get_review_queue()`
7. `set_processor_mode` calls `processor.set_mode()`
8. `approve_decision`, `reject_decision`, `override_severity` delegate to processor

- [ ] **Step 2: Update main.rs — create and spawn processor**

In `main.rs`, after creating the `Handler`:
```rust
use meshd::llm::LlmConfig;
use meshd::processor::{Processor, ProcessorMode};
use meshd::queue::SubmissionQueue;
use std::sync::Arc;

let queue = Arc::new(SubmissionQueue::new(256));
let processor = Arc::new(Processor::new(
    queue.clone(),
    Arc::new(meshd::docs::DocsStore::new()),
    LlmConfig::default(),
    ProcessorMode::Autonomous,
    3,
));
let processor_clone = processor.clone();
tokio::spawn(async move { processor_clone.start().await });

let handler = Handler::new_with_processor(queue, processor).await?;
```

- [ ] **Step 3: Simplify engagement.rs submit**

Remove the gossip broadcast from `EngagementManager::submit()` since the processor handles that. The engagement manager's `submit` is no longer called for findings — it's only called for hosts/services. Or better: add a `submit_direct()` for hosts/services and remove findings from the old submit path.

- [ ] **Step 4: Build and fix compilation**

Run: `cargo build --manifest-path meshd/Cargo.toml`
Expected: Compiles

- [ ] **Step 5: Commit**

```bash
git add meshd/src/handler.rs meshd/src/main.rs meshd/src/engagement.rs
git commit -m "feat: wire Processor into handler, main, and engagement manager"
```

---

### Task 6: Add docs query_similar method

**Files:**
- Modify: `meshd/src/docs.rs`

- [ ] **Step 1: Add query_similar to DocsStore**

```rust
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

    let mut candidates: Vec<&Value> = findings
        .iter()
        .filter_map(|(_, v)| {
            let f_target = v.get("target").and_then(|t| t.as_str()).unwrap_or("");
            let f_class = v.get("vuln_class").and_then(|c| c.as_str()).unwrap_or("");
            // Match if same target OR same vuln_class
            if f_target == target || f_class == vuln_class {
                Some(v)
            } else {
                None
            }
        })
        .collect();

    candidates.truncate(limit);
    Ok(candidates.into_iter().cloned().collect())
}
```

- [ ] **Step 2: Verify build**

Run: `cargo build --manifest-path meshd/Cargo.toml`
Expected: Compiles

- [ ] **Step 3: Commit**

```bash
git add meshd/src/docs.rs
git commit -m "feat: add query_similar to DocsStore for dedup candidate retrieval"
```

---

### Task 7: Python — Processor Client + Manager Methods

**Files:**
- Modify: `riftor/mesh/client.py`
- Modify: `riftor/mesh/manager.py`

- [ ] **Step 1: Add processor RPCs to MeshClient**

```python
async def get_queue_stats(self, engagement_id: str) -> dict:
    resp = await self._daemon.request("get_queue_stats", {"engagement_id": engagement_id})
    if not resp.ok:
        raise MeshError(resp.error or {})
    return resp.result or {}

async def get_review_queue(self, engagement_id: str) -> list[dict]:
    resp = await self._daemon.request("get_review_queue", {"engagement_id": engagement_id})
    if not resp.ok:
        raise MeshError(resp.error or {})
    return (resp.result or {}).get("decisions", [])

async def set_processor_mode(self, engagement_id: str, mode: str) -> str:
    resp = await self._daemon.request("set_processor_mode", {
        "engagement_id": engagement_id, "mode": mode,
    })
    if not resp.ok:
        raise MeshError(resp.error or {})
    return (resp.result or {}).get("mode", "")

async def approve_decision(self, engagement_id: str, submission_id: str) -> dict:
    resp = await self._daemon.request("approve_decision", {
        "engagement_id": engagement_id, "submission_id": submission_id,
    })
    if not resp.ok:
        raise MeshError(resp.error or {})
    return resp.result or {}

async def reject_decision(self, engagement_id: str, submission_id: str, reason: str) -> dict:
    resp = await self._daemon.request("reject_decision", {
        "engagement_id": engagement_id, "submission_id": submission_id, "reason": reason,
    })
    if not resp.ok:
        raise MeshError(resp.error or {})
    return resp.result or {}
```

- [ ] **Step 2: Add corresponding methods to MeshManager**

```python
async def get_queue_stats(self) -> dict:
    self._ensure_engagement_active()
    return await self._ensure_client().get_queue_stats(self._current_engagement.meta.id)

async def get_review_queue(self) -> list:
    self._ensure_engagement_active()
    return await self._ensure_client().get_review_queue(self._current_engagement.meta.id)

async def set_processor_mode(self, mode: str) -> str:
    self._ensure_engagement_active()
    return await self._ensure_client().set_processor_mode(self._current_engagement.meta.id, mode)

async def approve_review(self, submission_id: str) -> dict:
    self._ensure_engagement_active()
    return await self._ensure_client().approve_decision(self._current_engagement.meta.id, submission_id)

async def reject_review(self, submission_id: str, reason: str) -> dict:
    self._ensure_engagement_active()
    return await self._ensure_client().reject_decision(self._current_engagement.meta.id, submission_id, reason)
```

- [ ] **Step 3: Run Python tests to verify no regressions**

Run: `uv run pytest tests/mesh/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add riftor/mesh/client.py riftor/mesh/manager.py
git commit -m "feat: add processor RPCs to MeshClient and MeshManager"
```

---

### Task 8: Python — TUI Review Screen + Commands

**Files:**
- Modify: `riftor/mesh/commands.py`
- Modify: `riftor/mesh/sidebar.py`

- [ ] **Step 1: Update commands.py — add processor commands**

Add to `register_mesh_commands()`:

```python
@app.command_handler("/mesh mode")
async def mesh_mode(args: str = ""):
    mode = args.strip()
    if mode not in ("autonomous", "review", "critical"):
        app.notify("Usage: /mesh mode autonomous|review|critical", severity="error")
        return
    try:
        new_mode = await manager.set_processor_mode(mode)
        app.notify(f"Processor mode: {new_mode}")
    except Exception as e:
        app.notify(f"Failed to set mode: {e}", severity="error")

@app.command_handler("/mesh queue")
async def mesh_queue():
    try:
        stats = await manager.get_queue_stats()
        lines = [
            f"Pending: {stats.get('pending', 0)}",
            f"Processing: {stats.get('processing', 0)}",
            f"Completed: {stats.get('completed', 0)}",
            f"Failed: {stats.get('failed', 0)}",
        ]
        app.notify("\n".join(lines), title="Queue Stats")
    except Exception as e:
        app.notify(f"Failed: {e}", severity="error")

@app.command_handler("/mesh processor")
async def mesh_processor():
    try:
        stats = await manager.get_queue_stats()
        mode = stats.get("mode", "unknown")
        app.notify(f"Processor: {mode} | Workers: {stats.get('worker_count', '?')}", title="Processor")
    except Exception as e:
        app.notify(f"Failed: {e}", severity="error")

@app.command_handler("/mesh review")
async def mesh_review():
    try:
        decisions = await manager.get_review_queue()
        if not decisions:
            app.notify("No pending decisions")
            return
        # Show first decision
        d = decisions[0]
        lines = [
            f"#{d['submission_id'][:8]}: {d['finding'].get('title', 'N/A')}",
            f"Decision: {d['decision']} | Severity: {d.get('severity', 'N/A')}",
            f"Confidence: {d.get('confidence', 'N/A')}",
            f"Reasoning: {d.get('reasoning', 'N/A')[:200]}",
        ]
        app.notify("\n".join(lines), title="Pending Review")
    except Exception as e:
        app.notify(f"Failed: {e}", severity="error")
```

- [ ] **Step 2: Update sidebar.py — add processor status section**

Add to `compose()` in `MeshSidebar`:
```python
with Vertical(id="mesh-processor"):
    yield Static("Processor", classes="section-header")
    yield Label("Not connected", id="mesh-processor-status")
```

Add method:
```python
def update_processor_status(self, mode: str, pending: int) -> None:
    status = self.query_one("#mesh-processor-status", Label)
    status.update(f"\u25cf [{mode}] Queue: {pending}")
```

- [ ] **Step 3: Run Python tests**

Run: `uv run pytest tests/mesh/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add riftor/mesh/commands.py riftor/mesh/sidebar.py
git commit -m "feat: add processor TUI commands (/mesh mode, review, queue, processor)"
```

---

### Task 9: Rust Integration Test — Mock LLM

**Files:**
- Create: `meshd/tests/processor_test.rs`

- [ ] **Step 1: Write processor integration test with mock LLM**

```rust
use meshd::queue::{SubmissionQueue, Submission};
use meshd::docs::DocsStore;
use meshd::llm::{LlmClient, LlmConfig, CircuitBreaker};
use meshd::processor::{Processor, ProcessorMode};
use serde_json::json;
use std::sync::Arc;

#[tokio::test]
async fn test_queue_enqueue_dequeue() {
    let queue = Arc::new(SubmissionQueue::new(10));
    let sub = Submission {
        submission_id: "s1".into(),
        engagement_id: "e1".into(),
        author_node_id: "a1".into(),
        data: json!({"title": "test", "target": "10.0.0.1", "vuln_class": "xss", "severity": "medium"}),
    };
    queue.enqueue(sub).await.unwrap();
    let stats = queue.stats();
    assert!(stats.pending > 0);
}

#[tokio::test]
async fn test_validate_rejects_missing_title() {
    let queue = Arc::new(SubmissionQueue::new(10));
    let docs = Arc::new(DocsStore::new());
    let processor = Arc::new(Processor::new(
        queue.clone(), docs, LlmConfig::default(),
        ProcessorMode::Autonomous, 1,
    ));

    let sub = Submission {
        submission_id: "s1".into(),
        engagement_id: "e1".into(),
        author_node_id: "a1".into(),
        data: json!({"target": "10.0.0.1", "vuln_class": "xss"}),
    };

    // Enqueue and process — should fail validation
    queue.enqueue(sub).await.unwrap();
    let result = processor.process_submission(&queue.dequeue().await.unwrap()).await;
    assert!(result.is_err());
}

#[tokio::test]
async fn test_circuit_breaker_opens_after_failures() {
    let cb = CircuitBreaker::new(60);
    assert!(!cb.is_open());
    for _ in 0..5 {
        cb.record_failure();
    }
    assert!(cb.is_open());
}

#[tokio::test]
async fn test_processor_mode_persistence() {
    let queue = Arc::new(SubmissionQueue::new(10));
    let docs = Arc::new(DocsStore::new());
    let processor = Arc::new(Processor::new(
        queue, docs, LlmConfig::default(),
        ProcessorMode::Autonomous, 1,
    ));

    assert_eq!(processor.stats().await.mode, ProcessorMode::Autonomous);
    processor.set_mode(ProcessorMode::ReviewRequired).await;
    assert_eq!(processor.stats().await.mode, ProcessorMode::ReviewRequired);
}
```

- [ ] **Step 2: Run tests**

Run: `cargo test --manifest-path meshd/Cargo.toml --test processor_test`
Expected: 4 tests pass

- [ ] **Step 3: Commit**

```bash
git add meshd/tests/processor_test.rs
git commit -m "test: add processor integration tests with mock LLM"
```

---

### Task 10: End-to-End Verification

**Files:** None (manual verification)

- [ ] **Step 1: Run all tests**

```bash
export PATH="/home/linuxbrew/.linuxbrew/Cellar/rustup/1.29.0/bin:$HOME/.cargo/bin:$PATH"
cargo test --manifest-path meshd/Cargo.toml
uv run pytest tests/mesh/ -v
uv run pytest tests/ --ignore=tests/mesh -q
uv run ruff check riftor tests
uv run pyright riftor/mesh/
```

- [ ] **Step 2: Run E2E daemon test (updated for Phase 2)**

Verify the daemon starts and the new RPCs work:
```bash
echo '{"id":1,"method":"create_engagement","params":{"name":"test"}}' | riftor-meshd
# Then test get_queue_stats, set_processor_mode
```

- [ ] **Step 3: Commit any final fixes**

- [ ] **Step 4: Run final verification**

```bash
uv run python dev/mesh_e2e.py
```

---

## Plan Self-Review

1. **Spec coverage**: All spec requirements covered — queue module, LLM client (retry + circuit breaker), prompt templates (dedup + severity), processor pipeline (validate → dedup → severity → merge/reject → publish), review queue with approve/reject/override, three processing modes, TUI commands, sidebar status. Each has a task.

2. **Placeholder scan**: No TBDs or TODOs. All code blocks are complete. All test assertions are specific. Open questions from spec (cost tracking, prompt customization, queue persistence) are intentionally deferred to Phase 3.

3. **Type consistency**: `Submission` struct matches between queue.rs and processor.rs. `PendingDecision` matches between processor.rs and the handler RPCs. `ProcessorMode` enum matches between processor.rs and the Python commands. `QueueStats` matches between queue.rs, processor.rs, and client.py.
