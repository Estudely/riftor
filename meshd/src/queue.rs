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
            pending: (self.tx.max_capacity() as u64).saturating_sub(self.tx.capacity() as u64),
            processing: self.processing.load(Ordering::Relaxed),
            completed: self.completed.load(Ordering::Relaxed),
            failed: self.failed.load(Ordering::Relaxed),
        }
    }
}

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
