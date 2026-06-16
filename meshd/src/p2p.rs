use iroh::endpoint::{Endpoint, RecvStream, SendStream};
use iroh::protocol::{ProtocolHandler, Router};
use iroh::EndpointId;
use serde_json::Value;
use std::sync::Arc;
use tracing::{info, warn};

/// ALPN for the riftor-mesh protocol
pub const ALPN: &[u8] = b"riftor-mesh/0";

/// P2P protocol handler that routes incoming messages to the Commander's pipeline.
#[derive(Clone)]
pub struct MeshProtocolHandler {
    /// Submissions from P2P peers are enqueued here for the AI processor
    pub queue: Option<Arc<crate::queue::SubmissionQueue>>,
    /// State queries (get_state) read from this shared doc store
    pub docs: Option<Arc<crate::docs::DocsStore>>,
}

impl std::fmt::Debug for MeshProtocolHandler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MeshProtocolHandler").finish()
    }
}

impl ProtocolHandler for MeshProtocolHandler {
    async fn accept(&self, conn: iroh::endpoint::Connection) -> Result<(), iroh::protocol::AcceptError> {
        info!("P2P connection accepted");
        let queue = self.queue.clone();
        let docs = self.docs.clone();

        while let Ok((mut send, mut recv)) = conn.accept_bi().await {
            let q = queue.clone();
            let d = docs.clone();
            tokio::spawn(async move {
                // Read line by line (JSON-line protocol)
                let mut buf = [0u8; 8192];
                let mut line_buf = Vec::new();
                loop {
                    match recv.read(&mut buf).await {
                        Ok(Some(n)) => {
                            for &byte in &buf[..n] {
                                if byte == b'\n' {
                                    // Complete line received
                                    let line = String::from_utf8_lossy(&line_buf);
                                    let response = handle_p2p_message(&line, &q, &d).await;
                                    let _ = send.write_all(&response).await;
                                    line_buf.clear();
                                } else {
                                    line_buf.push(byte);
                                }
                            }
                        }
                        _ => break,
                    }
                }
            });
        }
        Ok(())
    }
}

/// Process a P2P message and return a JSON response.
async fn handle_p2p_message(
    line: &str,
    queue: &Option<Arc<crate::queue::SubmissionQueue>>,
    docs: &Option<Arc<crate::docs::DocsStore>>,
) -> Vec<u8> {
    let msg: Value = match serde_json::from_str(line) {
        Ok(m) => m,
        Err(e) => {
            let err = serde_json::json!({"error": {"code": "PARSE_ERROR", "message": e.to_string()}});
            return serde_json::to_vec(&err).unwrap_or_default();
        }
    };

    let method = msg.get("method").and_then(|m| m.as_str()).unwrap_or("");
    let id = msg.get("id").and_then(|i| i.as_u64()).unwrap_or(0);
    let params = msg.get("params").cloned().unwrap_or(Value::Null);

    match method {
        "ping" => {
            serde_json::to_vec(&serde_json::json!({"id": id, "result": {"pong": true}})).unwrap_or_default()
        }

        "submit" => {
            match &queue {
                Some(q) => {
                    let engagement_id = params.get("engagement_id")
                        .and_then(|v| v.as_str()).unwrap_or("").to_string();
                    let submission_data = params.get("submission")
                        .and_then(|s| s.get("data")).cloned()
                        .unwrap_or(Value::Null);

                    let sub = crate::queue::Submission {
                        submission_id: uuid::Uuid::new_v4().to_string(),
                        engagement_id: engagement_id.clone(),
                        author_node_id: "p2p-peer".into(),
                        data: submission_data,
                    };
                    let submission_id = sub.submission_id.clone();
                    match q.enqueue(sub).await {
                        Ok(()) => {
                            info!("P2P submission enqueued: {}", submission_id);
                            serde_json::to_vec(&serde_json::json!({
                                "id": id, "result": {"submission_id": submission_id, "status": "queued"}
                            })).unwrap_or_default()
                        }
                        Err(e) => {
                            serde_json::to_vec(&serde_json::json!({
                                "id": id, "error": {"code": "QUEUE_FULL", "message": e.to_string()}
                            })).unwrap_or_default()
                        }
                    }
                }
                None => {
                    serde_json::to_vec(&serde_json::json!({
                        "id": id, "error": {"code": "NOT_COMMANDER", "message": "This node is not a Commander"}
                    })).unwrap_or_default()
                }
            }
        }

        "get_state" => {
            let engagement_id = params.get("engagement_id")
                .and_then(|v| v.as_str()).unwrap_or("");
            match docs {
                Some(d) => {
                    let findings = d.get_all(engagement_id, "finding").await.unwrap_or_default();
                    let hosts = d.get_all(engagement_id, "host").await.unwrap_or_default();
                    let services = d.get_all(engagement_id, "service").await.unwrap_or_default();
                    serde_json::to_vec(&serde_json::json!({
                        "id": id, "result": {"findings": findings, "hosts": hosts, "services": services}
                    })).unwrap_or_default()
                }
                None => {
                    serde_json::to_vec(&serde_json::json!({
                        "id": id, "error": {"code": "NOT_COMMANDER", "message": "This node is not a Commander"}
                    })).unwrap_or_default()
                }
            }
        }

        _ => {
            serde_json::to_vec(&serde_json::json!({
                "id": id, "error": {"code": "UNKNOWN_METHOD", "message": format!("Unknown P2P method: {}", method)}
            })).unwrap_or_default()
        }
    }
}

/// Start the P2P router, taking ownership of the endpoint.
pub fn spawn_router(
    endpoint: Endpoint,
    queue: Option<Arc<crate::queue::SubmissionQueue>>,
    docs: Option<Arc<crate::docs::DocsStore>>,
) -> Router {
    let handler = Arc::new(MeshProtocolHandler { queue, docs });
    iroh::protocol::Router::builder(endpoint)
        .accept(ALPN.to_vec(), handler)
        .spawn()
}

/// Connect to a remote peer and return a P2pStream.
pub async fn dial(
    endpoint: &Endpoint,
    endpoint_id: EndpointId,
    addrs: Vec<iroh::TransportAddr>,
    relay_url: Option<iroh::RelayUrl>,
) -> anyhow::Result<P2pStream> {
    let mut endpoint_addr = iroh::EndpointAddr::from(endpoint_id)
        .with_addrs(addrs);
    if let Some(url) = relay_url {
        endpoint_addr = endpoint_addr.with_relay_url(url);
    }
    let conn = endpoint.connect(endpoint_addr, ALPN).await?;
    let (send, recv) = conn.open_bi().await?;
    Ok(P2pStream { send, recv })
}

/// A bidirectional P2P stream that speaks JSON-line protocol.
pub struct P2pStream {
    send: SendStream,
    recv: RecvStream,
}

impl P2pStream {
    pub async fn send_json(&mut self, value: &Value) -> anyhow::Result<()> {
        let mut data = serde_json::to_vec(value)?;
        data.push(b'\n');
        self.send.write_all(&data).await?;
        Ok(())
    }

    pub async fn recv_json(&mut self) -> anyhow::Result<Value> {
        let line = self.recv_line().await?;
        let value: Value = serde_json::from_str(&line)?;
        Ok(value)
    }

    pub async fn send_line(&mut self, line: &str) -> anyhow::Result<()> {
        self.send.write_all(line.as_bytes()).await?;
        self.send.write_all(b"\n").await?;
        Ok(())
    }

    pub async fn recv_line(&mut self) -> anyhow::Result<String> {
        let mut line = String::new();
        let mut buf = [0u8; 1];
        loop {
            match self.recv.read(&mut buf).await? {
                Some(0) => anyhow::bail!("connection closed"),
                Some(_) => {
                    if buf[0] == b'\n' { break; }
                    line.push(buf[0] as char);
                }
                None => anyhow::bail!("stream closed"),
            }
        }
        Ok(line)
    }
}
