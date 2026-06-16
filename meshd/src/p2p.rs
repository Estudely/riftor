use iroh::endpoint::{Endpoint, RecvStream, SendStream};
use iroh::protocol::{ProtocolHandler, Router};
use iroh::EndpointId;
use serde_json::Value;
use std::sync::Arc;
use tracing::info;

/// ALPN for the riftor-mesh protocol
pub const ALPN: &[u8] = b"riftor-mesh/0";

/// A protocol handler that echoes incoming P2P streams.
/// In production, this routes to the Handler for full JSON-line processing.
#[derive(Clone)]
pub struct MeshProtocolHandler {
    pub node_id: String,
}

impl std::fmt::Debug for MeshProtocolHandler {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("MeshProtocolHandler")
            .field("node_id", &self.node_id)
            .finish()
    }
}

impl ProtocolHandler for MeshProtocolHandler {
    async fn accept(&self, conn: iroh::endpoint::Connection) -> Result<(), iroh::protocol::AcceptError> {
        info!("P2P connection accepted");
        while let Ok((mut send, mut recv)) = conn.accept_bi().await {
            tokio::spawn(async move {
                let mut buf = [0u8; 4096];
                loop {
                    match recv.read(&mut buf).await {
                        Ok(Some(n)) => {
                            // Echo for now — in production, parse JSON and route to handler
                            if let Ok(msg) = serde_json::from_slice::<Value>(&buf[..n]) {
                                info!("P2P received: {}", msg);
                                let resp = serde_json::json!({"result": {"pong": true}});
                                if let Ok(mut data) = serde_json::to_vec(&resp) {
                                    data.push(b'\n');
                                    let _ = send.write_all(&data).await;
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

/// Start the P2P router, taking ownership of the endpoint.
/// Returns the Router handle (must be kept alive).
pub fn spawn_router(endpoint: Endpoint) -> Router {
    let handler = Arc::new(MeshProtocolHandler {
        node_id: "router".into(),
    });
    iroh::protocol::Router::builder(endpoint)
        .accept(ALPN.to_vec(), handler)
        .spawn()
}

/// Connect to a remote peer and return a P2pStream.
pub async fn dial(
    endpoint: &Endpoint,
    endpoint_id: EndpointId,
    relay_url: Option<iroh::RelayUrl>,
) -> anyhow::Result<P2pStream> {
    let mut endpoint_addr = iroh::EndpointAddr::from(endpoint_id);
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
    /// Send a JSON-line message.
    pub async fn send_json(&mut self, value: &Value) -> anyhow::Result<()> {
        let mut data = serde_json::to_vec(value)?;
        data.push(b'\n');
        self.send.write_all(&data).await?;
        Ok(())
    }

    /// Receive a JSON-line message.
    pub async fn recv_json(&mut self) -> anyhow::Result<Value> {
        let line = self.recv_line().await?;
        let value: Value = serde_json::from_str(&line)?;
        Ok(value)
    }

    /// Send a raw line.
    pub async fn send_line(&mut self, line: &str) -> anyhow::Result<()> {
        self.send.write_all(line.as_bytes()).await?;
        self.send.write_all(b"\n").await?;
        Ok(())
    }

    /// Receive a raw line.
    pub async fn recv_line(&mut self) -> anyhow::Result<String> {
        let mut line = String::new();
        let mut buf = [0u8; 1];
        loop {
            match self.recv.read(&mut buf).await? {
                Some(0) => anyhow::bail!("connection closed"),
                Some(_) => {
                    if buf[0] == b'\n' {
                        break;
                    }
                    line.push(buf[0] as char);
                }
                None => anyhow::bail!("stream closed"),
            }
        }
        Ok(line)
    }
}
