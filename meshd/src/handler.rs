use crate::protocol::{Response, ResponseError, Event};
use serde_json::{json, Value};
use tokio::sync::broadcast;
use tracing::warn;

pub struct Handler {
    identity_manager: crate::identity::IdentityManager,
    engagement_manager: crate::engagement::EngagementManager,
    event_tx: broadcast::Sender<String>,
}

impl Handler {
    pub async fn new() -> anyhow::Result<Self> {
        let (event_tx, _) = broadcast::channel(256);
        let identity_manager = crate::identity::IdentityManager::load_or_create().await?;
        let node_id = identity_manager.get_info()?.node_id;
        let engagement_manager = crate::engagement::EngagementManager::new(node_id);

        Ok(Self {
            identity_manager,
            engagement_manager,
            event_tx,
        })
    }

    pub async fn handle(&self, request: crate::protocol::Request) -> Response {
        match request.method.as_str() {
            "create_identity" => self.create_identity(request.id).await,
            "create_engagement" => self.create_engagement(request.id, request.params).await,
            "generate_invite" => self.generate_invite(request.id, request.params).await,
            "join_engagement" => self.join_engagement(request.id, request.params).await,
            "leave_engagement" => self.leave_engagement(request.id, request.params).await,
            "submit" => self.submit(request.id, request.params).await,
            "get_state" => self.get_state(request.id, request.params).await,
            "add_blob" => self.add_blob(request.id, request.params).await,
            "get_blob" => self.get_blob(request.id, request.params).await,
            "ping" => Response::Success { id: request.id, result: json!({"pong": true}) },
            method => Response::Error {
                id: request.id,
                error: ResponseError {
                    code: "UNKNOWN_METHOD".into(),
                    message: format!("Unknown method: {}", method),
                },
            },
        }
    }

    async fn create_identity(&self, id: u64) -> Response {
        match self.identity_manager.get_info() {
            Ok(info) => Response::Success { id, result: json!(info) },
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "IDENTITY_ERROR".into(), message: e.to_string() },
            },
        }
    }

    async fn create_engagement(&self, id: u64, params: Value) -> Response {
        let name = match params.get("name").and_then(|v| v.as_str()) {
            Some(n) => n.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'name'".into() },
            },
        };

        match self.engagement_manager.create(name).await {
            Ok(meta) => {
                if let Ok(data) = serde_json::to_value(&meta) {
                    let event = Event {
                        event: "engagement_created".into(),
                        data,
                    };
                    if let Ok(json) = crate::protocol::write_event(&event) {
                        let _ = self.event_tx.send(json);
                    } else {
                        warn!("Failed to serialize event for engagement_created");
                    }
                } else {
                    warn!("Failed to serialize engagement meta for event");
                }
                let result = match serde_json::to_value(meta) {
                    Ok(r) => r,
                    Err(e) => {
                        warn!(%e, "Failed to serialize response meta");
                        return Response::Error {
                            id,
                            error: ResponseError {
                                code: "INTERNAL_ERROR".into(),
                                message: "Failed to serialize response".into(),
                            },
                        };
                    }
                };
                Response::Success { id, result }
            }
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "ENGAGEMENT_ERROR".into(), message: e.to_string() },
            },
        }
    }

    async fn generate_invite(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'engagement_id'".into() },
            },
        };

        match self.engagement_manager.generate_invite(&engagement_id).await {
            Ok(invite) => Response::Success { id, result: json!({"invite": invite}) },
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "INVITE_ERROR".into(), message: e.to_string() },
            },
        }
    }

    async fn join_engagement(&self, id: u64, params: Value) -> Response {
        let invite = match params.get("invite").and_then(|v| v.as_str()) {
            Some(i) => i.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'invite'".into() },
            },
        };

        match self.engagement_manager.join(&invite).await {
            Ok(meta) => {
                if let Ok(data) = serde_json::to_value(&meta) {
                    let event = Event {
                        event: "engagement_joined".into(),
                        data,
                    };
                    if let Ok(json) = crate::protocol::write_event(&event) {
                        let _ = self.event_tx.send(json);
                    } else {
                        warn!("Failed to serialize event for engagement_joined");
                    }
                } else {
                    warn!("Failed to serialize engagement meta for event");
                }
                let result = match serde_json::to_value(meta) {
                    Ok(r) => r,
                    Err(e) => {
                        warn!(%e, "Failed to serialize response meta");
                        return Response::Error {
                            id,
                            error: ResponseError {
                                code: "INTERNAL_ERROR".into(),
                                message: "Failed to serialize response".into(),
                            },
                        };
                    }
                };
                Response::Success { id, result }
            }
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "JOIN_ERROR".into(), message: e.to_string() },
            },
        }
    }

    async fn leave_engagement(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'engagement_id'".into() },
            },
        };

        match self.engagement_manager.leave(&engagement_id).await {
            Ok(()) => Response::Success { id, result: json!({"left": true}) },
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "LEAVE_ERROR".into(), message: e.to_string() },
            },
        }
    }

    async fn submit(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'engagement_id'".into() },
            },
        };

        let submission = match params.get("submission") {
            Some(s) => s,
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'submission'".into() },
            },
        };

        match self.engagement_manager.submit(&engagement_id, submission).await {
            Ok(submission_id) => Response::Success { id, result: json!({"submission_id": submission_id}) },
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "SUBMIT_ERROR".into(), message: e.to_string() },
            },
        }
    }

    async fn get_state(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'engagement_id'".into() },
            },
        };

        match self.engagement_manager.get_state(&engagement_id).await {
            Ok(state) => Response::Success { id, result: state },
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "STATE_ERROR".into(), message: e.to_string() },
            },
        }
    }

    async fn add_blob(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'engagement_id'".into() },
            },
        };

        let data_b64 = match params.get("data").and_then(|v| v.as_str()) {
            Some(d) => d,
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'data'".into() },
            },
        };

        use base64::Engine;
        let data = match base64::engine::general_purpose::STANDARD.decode(data_b64) {
            Ok(d) => d,
            Err(e) => return Response::Error {
                id,
                error: ResponseError { code: "BASE64_ERROR".into(), message: e.to_string() },
            },
        };

        match crate::blobs::add_blob(&engagement_id, &data).await {
            Ok(hash) => Response::Success { id, result: json!({"hash": hash}) },
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "BLOB_ERROR".into(), message: e.to_string() },
            },
        }
    }

    async fn get_blob(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'engagement_id'".into() },
            },
        };

        let hash = match params.get("hash").and_then(|v| v.as_str()) {
            Some(h) => h.to_string(),
            None => return Response::Error {
                id,
                error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'hash'".into() },
            },
        };

        match crate::blobs::get_blob(&engagement_id, &hash).await {
            Ok(data) => {
                use base64::Engine;
                let b64 = base64::engine::general_purpose::STANDARD.encode(&data);
                Response::Success { id, result: json!({"data": b64}) }
            }
            Err(e) => Response::Error {
                id,
                error: ResponseError { code: "BLOB_ERROR".into(), message: e.to_string() },
            },
        }
    }

    pub fn event_rx(&self) -> broadcast::Receiver<String> {
        self.event_tx.subscribe()
    }
}
