use iroh::endpoint::Endpoint;
use iroh::protocol::ProtocolHandler;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

#[derive(Debug, Clone)]
struct EchoHandler;

impl ProtocolHandler for EchoHandler {
    async fn accept(&self, connection: iroh::endpoint::Connection) -> Result<(), iroh::protocol::AcceptError> {
        while let Ok((mut send, mut recv)) = connection.accept_bi().await {
            tokio::spawn(async move {
                let mut buf = [0u8; 1024];
                loop {
                    match recv.read(&mut buf).await {
                        Ok(Some(n)) => {
                            let _ = send.write_all(&buf[..n]).await;
                        }
                        _ => break,
                    }
                }
            });
        }
        Ok(())
    }
}

#[tokio::test]
async fn test_p2p_echo_between_two_endpoints() {
    let ep_a = Endpoint::builder(iroh::endpoint::presets::Minimal)
        .bind().await.expect("ep_a bind");
    let ep_b = Endpoint::builder(iroh::endpoint::presets::Minimal)
        .bind().await.expect("ep_b bind");

    let node_addr_a = ep_a.addr();
    eprintln!("A: {:?}", node_addr_a);

    // Spawn router on endpoint A with echo handler
    let _router_a = iroh::protocol::Router::builder(ep_a)
        .accept(b"riftor-mesh/0".to_vec(), Arc::new(EchoHandler))
        .spawn();

    sleep(Duration::from_millis(200)).await;

    // B connects to A
    let conn = ep_b.connect(node_addr_a, b"riftor-mesh/0").await
        .expect("B connects to A");
    let (mut send, mut recv) = conn.open_bi().await.expect("open bi");

    send.write_all(b"hello p2p\n").await.expect("send");
    let mut buf = [0u8; 1024];
    let n = recv.read(&mut buf).await.expect("recv").expect("data");
    let response = String::from_utf8_lossy(&buf[..n]);
    assert_eq!(response.trim(), "hello p2p");
    eprintln!("P2P echo: {}", response.trim());
}
