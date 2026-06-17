use crate::queue::{SubmissionQueue, Submission};
use crate::llm::{LlmClient, LlmConfig};
use crate::prompts;
use crate::docs::DocsStore;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::sync::Mutex;
use tracing::{info, error};

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
    pub pending: u64,
    pub processing: u64,
    pub completed: u64,
    pub failed: u64,
    pub circuit_open: bool,
    pub worker_count: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct PendingDecision {
    pub submission_id: String,
    pub engagement_id: String,
    pub finding: Value,
    pub decision: String,
    pub confidence: Option<f64>,
    pub matched_finding_id: Option<String>,
    pub severity: Option<String>,
    pub reasoning: Option<String>,
}

pub struct Processor {
    pub queue: Arc<SubmissionQueue>,
    docs: Arc<DocsStore>,
    llm: Arc<LlmClient>,
    mode: Mutex<ProcessorMode>,
    review_queue: Mutex<Vec<PendingDecision>>,
    worker_count: usize,
    gossip: Option<Arc<crate::gossip::GossipStore>>,
}

impl Processor {
    pub fn new(
        queue: Arc<SubmissionQueue>,
        docs: Arc<DocsStore>,
        llm_config: LlmConfig,
        mode: ProcessorMode,
        worker_count: usize,
        gossip: Option<Arc<crate::gossip::GossipStore>>,
    ) -> Self {
        let llm = Arc::new(LlmClient::new(llm_config));
        Self {
            queue,
            docs,
            llm,
            mode: Mutex::new(mode),
            review_queue: Mutex::new(Vec::new()),
            worker_count,
            gossip,
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

        // Stage 2: Get existing findings for dedup
        let existing_findings = self.docs.get_all(&submission.engagement_id, "finding").await?;

        // Stage 3: Deduplicate (only if there are existing findings)
        let (decision, confidence, matched_id, dedup_reasoning) = if existing_findings.is_empty() {
            ("new".to_string(), None, None, Some("No existing findings to compare against".to_string()))
        } else {
            let dedup_user = prompts::build_dedup_user(
                &serde_json::to_string(&finding)?,
                &serde_json::to_string(&existing_findings)?,
            );
            match self.llm.complete(prompts::DEDUP_SYSTEM, &dedup_user).await {
                Ok(result) => {
                    let d = result["decision"].as_str().unwrap_or("new").to_string();
                    let c = result["confidence"].as_f64();
                    let m = result["matched_finding_id"].as_str().map(|s| s.to_string());
                    let r = result["reasoning"].as_str().map(|s| s.to_string());
                    (d, c, m, r)
                }
                Err(_) => {
                    // LLM failed — queue for human review
                    return Ok(PendingDecision {
                        submission_id: submission.submission_id.clone(),
                        engagement_id: submission.engagement_id.clone(),
                        finding: finding.clone(),
                        decision: "manual_review_needed".into(),
                        confidence: None,
                        matched_finding_id: None,
                        severity: None,
                        reasoning: Some("LLM dedup call failed — needs manual review".into()),
                    });
                }
            }
        };

        // Stage 4: Reject vague submissions
        if confidence.is_some_and(|c| c < 0.2) {
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

        // Stage 5: Assess severity or merge
        if decision == "new" {
            let sev_user = prompts::build_severity_user(
                &serde_json::to_string(&finding)?,
                &json!({"asset_criticality": "unknown"}).to_string(),
            );
            let (severity, sev_reasoning) = match self.llm.complete(prompts::SEVERITY_SYSTEM, &sev_user).await {
                Ok(result) => {
                    let s = result["severity"].as_str().unwrap_or("medium").to_string();
                    let r = result["reasoning"].as_str().map(|s| s.to_string());
                    (s, r)
                }
                Err(_) => ("medium".to_string(), None),
            };

            Ok(PendingDecision {
                submission_id: submission.submission_id.clone(),
                engagement_id: submission.engagement_id.clone(),
                finding: finding.clone(),
                decision: "new".into(),
                confidence,
                matched_finding_id: None,
                severity: Some(severity),
                reasoning: sev_reasoning.or(dedup_reasoning),
            })
        } else if decision == "match" {
            Ok(PendingDecision {
                submission_id: submission.submission_id.clone(),
                engagement_id: submission.engagement_id.clone(),
                finding: finding.clone(),
                decision: "match".into(),
                confidence,
                matched_finding_id: matched_id,
                severity: None,
                reasoning: dedup_reasoning,
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
                reasoning: dedup_reasoning,
            })
        }
    }

    fn validate_submission(&self, submission: &Submission) -> anyhow::Result<()> {
        let f = &submission.data;
        if f["title"].as_str().unwrap_or("").is_empty() {
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
            info!("Decision queued for review: {}", decision.submission_id);
            queue.push(decision);
        } else {
            let finding_key = format!("finding/{}", decision.submission_id);
            let mut doc_value = decision.finding.clone();
            if let Some(sev) = &decision.severity {
                doc_value["severity"] = json!(sev);
            }
            doc_value["decision"] = json!(decision.decision);
            let _ = self.docs.insert(&engagement_id, "finding", &finding_key, doc_value).await;
            info!("Auto-published: {}", decision.submission_id);
            if let Some(gossip) = &self.gossip {
                let _ = gossip
                    .broadcast(
                        &engagement_id,
                        "processed",
                        serde_json::json!({"event": "finding_published", "key": finding_key}),
                    )
                    .await;
            }
        }
    }

    pub async fn set_mode(&self, mode: ProcessorMode) {
        let mut current = self.mode.lock().await;
        *current = mode.clone();
        info!("Processor mode set to {:?}", mode);
    }

    pub async fn stats(&self) -> ProcessorStats {
        let qs = self.queue.stats();
        ProcessorStats {
            mode: self.mode.lock().await.clone(),
            pending: qs.pending,
            processing: qs.processing,
            completed: qs.completed,
            failed: qs.failed,
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
            if let Some(gossip) = &self.gossip {
                let _ = gossip
                    .broadcast(
                        &decision.engagement_id,
                        "processed",
                        serde_json::json!({"event": "finding_published", "key": finding_key}),
                    )
                    .await;
            }
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
        drop(queue);
        self.approve_decision(submission_id).await
    }
}
