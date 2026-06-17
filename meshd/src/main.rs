use meshd::handler::Handler;
use meshd::identity::IdentityManager;
use meshd::protocol::{read_request, write_response, Response, ResponseError};
use std::io::{self, BufRead, Write};
use std::sync::Arc;
use tracing::{error, info};

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

    // --- Load persisted identity ---
    // The secret key is persisted to disk so the P2P NodeId stays stable across
    // restarts; otherwise invites would break every time the daemon relaunches.
    let identity = IdentityManager::load_or_create().await?;
    info!("Loaded persisted identity — NodeId: {}", identity.node_id());

    // --- Create iroh Endpoints ---
    // Router endpoint: handles incoming P2P protocol connections. Uses the
    // persisted secret key so peers can dial the same NodeId after a restart.
    let router_ep = iroh::endpoint::Endpoint::builder(iroh::endpoint::presets::Minimal)
        .secret_key(identity.secret_key().clone())
        .bind().await?;
    let node_id = router_ep.id();
    let router_addr = router_ep.addr();
    let relay_urls: Vec<String> = router_addr.relay_urls().map(|u| u.to_string()).collect();
    let direct_addrs: Vec<String> = router_addr.ip_addrs().map(|a| a.to_string()).collect();
    info!("iroh P2P endpoint bound — NodeId: {}", node_id);
    eprintln!("[riftor-meshd] NodeId: {}", node_id);
    eprintln!("[riftor-meshd] Relay URLs: {:?}", relay_urls);
    eprintln!("[riftor-meshd] Direct addresses: {:?}", direct_addrs);

    // Handler endpoint: used for outbound dials (separate port, shared internally by iroh)
    let handler_ep = Arc::new(
        iroh::endpoint::Endpoint::builder(iroh::endpoint::presets::Minimal)
            .bind().await?,
    );

    // Build the shared iroh stack (blobs + gossip + docs) on the router endpoint.
    let stack = meshd::mesh_stack::MeshStack::build(router_ep.clone()).await?;
    let blobs_api = stack.blobs_api();
    let docs_store = Arc::new(
        meshd::docs::DocsStore::new(stack.docs.clone(), blobs_api.clone()).await?,
    );
    let gossip_store = Arc::new(meshd::gossip::GossipStore::new(stack.gossip.clone()));

    // Channel for daemon-originated events (e.g. gossip-derived MeshEvents).
    // A dedicated task drains it to stdout as JSON lines so the Python TUI can
    // consume live updates. Created before the handler so the engagement
    // manager can be given the sink.
    let (event_tx, mut event_rx) =
        tokio::sync::mpsc::unbounded_channel::<meshd::protocol::Event>();

    // Pass P2P addresses to handler for get_node_addr RPC
    let handler = Handler::new(
        handler_ep.clone(),
        docs_store.clone(),
        gossip_store.clone(),
        node_id.to_string(),
        relay_urls,
        direct_addrs,
        Some(event_tx),
    )
    .await?;

    // Spawn P2P router — wired to the same queue and docs as the handler, and
    // hosting docs + gossip + blobs ALPNs on the SAME router endpoint alongside
    // riftor-mesh/0. The blobs ALPN is required so iroh-docs peers can download
    // entry *content* (not just metadata) during sync.
    let p2p_queue = handler.submission_queue();
    let p2p_docs = handler.doc_store();
    let blobs_proto = iroh_blobs::BlobsProtocol::new(&blobs_api, None);
    let _router = meshd::p2p::spawn_router(
        router_ep,
        Some(p2p_queue),
        Some(p2p_docs),
        stack.docs.clone(),
        stack.gossip.clone(),
        blobs_proto,
    );
    info!("P2P router started on ALPN: {:?}", String::from_utf8_lossy(meshd::p2p::ALPN));

    // Drain daemon-originated events to stdout as JSON lines. This task uses its
    // OWN stdout handle rather than sharing the request loop's locked handle:
    // the request loop holds an exclusive `io::stdout().lock()` for its whole
    // duration, so sharing would deadlock. Each event is a single complete JSON
    // line, and writes flush while the request loop is idle waiting on stdin —
    // good enough atomicity for the Python line-reader.
    tokio::spawn(async move {
        use std::io::Write;
        let mut out = std::io::stdout();
        while let Some(ev) = event_rx.recv().await {
            if let Ok(json) = serde_json::to_string(&ev) {
                let _ = writeln!(out, "{json}");
                let _ = out.flush();
            }
        }
    });

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

    // Keep daemon alive for P2P if RIFTOR_MESH_P2P env var is set
    if std::env::var("RIFTOR_MESH_P2P").is_ok() {
        info!("stdin closed, keeping daemon alive for P2P connections. Press Ctrl+C to stop.");
        tokio::signal::ctrl_c().await?;
    }

    info!("riftor-meshd shutting down");
    // Close endpoints gracefully so the iroh sockets and blobs store release
    // their resources instead of being dropped abruptly.
    let _ = _router.shutdown().await;
    handler_ep.close().await;
    Ok(())
}
