use meshd::handler::Handler;
use meshd::protocol::{read_request, write_response, Response, ResponseError};
use std::io::{self, BufRead, Write};
use tracing::{error, info};

use std::sync::Arc;

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

    // --- Create iroh Endpoint (manages its own keypair) ---
    let endpoint = Arc::new(
        iroh::endpoint::Endpoint::builder(iroh::endpoint::presets::Minimal)
            .bind()
            .await?,
    );

    let endpoint_addr = endpoint.addr();
    let node_id = endpoint.id();
    info!("iroh endpoint bound — NodeId: {}", node_id);
    eprintln!("[riftor-meshd] NodeId: {}", node_id);
    eprintln!(
        "[riftor-meshd] Relay URLs: {:?}",
        endpoint_addr.relay_urls().collect::<Vec<_>>()
    );
    eprintln!(
        "[riftor-meshd] Direct addresses: {:?}",
        endpoint_addr.ip_addrs().collect::<Vec<_>>()
    );

    let handler = Handler::new(endpoint).await?;

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
