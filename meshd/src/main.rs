use meshd::handler::Handler;
use meshd::p2p::{self, P2pStream};
use meshd::protocol::{read_request, write_response, Response, ResponseError};
use std::io::{self, BufRead, Write};
use std::sync::Arc;
use tracing::{error, info, warn};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("meshd=info".parse()?),
        )
        .with_writer(io::stderr)
        .init();

    info!("riftor-meshd starting");

    // --- Create iroh Endpoints ---
    // Router endpoint: handles incoming P2P protocol connections
    let router_ep = iroh::endpoint::Endpoint::builder(iroh::endpoint::presets::Minimal)
        .bind().await?;
    let node_id = router_ep.id();
    let router_addr = router_ep.addr();
    info!("iroh P2P endpoint bound — NodeId: {}", node_id);
    eprintln!("[riftor-meshd] NodeId: {}", node_id);
    eprintln!("[riftor-meshd] Relay URLs: {:?}", router_addr.relay_urls().collect::<Vec<_>>());
    eprintln!("[riftor-meshd] Direct addresses: {:?}", router_addr.ip_addrs().collect::<Vec<_>>());

    // Handler endpoint: used for outbound dials and get_node_addr
    let handler_ep = Arc::new(
        iroh::endpoint::Endpoint::builder(iroh::endpoint::presets::Minimal)
            .bind().await?,
    );

    let handler = Handler::new(handler_ep.clone()).await?;

    // Spawn P2P router
    let _router = meshd::p2p::spawn_router(router_ep);
    info!("P2P router started on ALPN: {:?}", String::from_utf8_lossy(meshd::p2p::ALPN));

    let stdin = io::stdin().lock();
    let mut stdout = io::stdout().lock();

    for line in stdin.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }

        match read_request(&line) {
            Ok(request) => {
                let response = handler.handle(request).await;
                let json = match write_response(&response) {
                    Ok(j) => j,
                    Err(e) => {
                        error!(%e, "serialize error");
                        continue;
                    }
                };
                writeln!(stdout, "{}", json)?;
                stdout.flush()?;
            }
            Err(e) => {
                error!(%e, "Failed to parse request");
                let response = Response::EventError {
                    error: ResponseError {
                        code: "PARSE_ERROR".to_string(),
                        message: e.to_string(),
                    },
                };
                let json = match write_response(&response) {
                    Ok(j) => j,
                    Err(e) => {
                        error!(%e, "serialize error");
                        continue;
                    }
                };
                writeln!(stdout, "{}", json)?;
                stdout.flush()?;
            }
        }
    }

    info!("riftor-meshd shutting down");
    Ok(())
}
