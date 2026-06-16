use meshd::handler::Handler;
use meshd::protocol::{read_request, write_response, Response, ResponseError};
use std::io::{self, BufRead, Write};
use tokio::task;
use tracing::{info, error};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env()
            .add_directive("meshd=info".parse()?))
        .with_writer(io::stderr)
        .init();

    info!("riftor-meshd starting");

    let handler = Handler::new().await?;
    let mut event_rx = handler.event_rx();

    task::spawn(async move {
        while let Ok(event_json) = event_rx.recv().await {
            writeln!(io::stdout(), "{}", event_json).ok();
            io::stdout().flush().ok();
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

    info!("riftor-meshd shutting down");
    Ok(())
}
