use crate::protocol::{Response, ResponseError};
use crate::processor::{Processor, ProcessorMode};
use crate::queue::{Submission, SubmissionQueue};
use serde_json::{json, Value};
use std::sync::Arc;
use tracing::warn;

pub struct Handler {
    identity_manager: crate::identity::IdentityManager,
    engagement_manager: crate::engagement::EngagementManager,
    processor: Arc<Processor>,
    /// Real iroh Endpoint for outbound P2P dials
    endpoint: Arc<iroh::endpoint::Endpoint>,
    /// P2P Router's addresses (for get_node_addr RPC)
    p2p_node_id: String,
    p2p_relay_urls: Vec<String>,
    p2p_direct_addrs: Vec<String>,
}

impl Handler {
    pub async fn new(
        endpoint: Arc<iroh::endpoint::Endpoint>,
        p2p_node_id: String,
        p2p_relay_urls: Vec<String>,
        p2p_direct_addrs: Vec<String>,
    ) -> anyhow::Result<Self> {
        let identity_manager = crate::identity::IdentityManager::load_or_create().await?;
        let node_id = endpoint.id().to_string();

        tracing::info!(
            "Loaded identity: node_id={} public_key={}",
            node_id,
            identity_manager.public_key()
        );

        let docs = Arc::new(crate::docs::DocsStore::new());
        let gossip = Arc::new(crate::gossip::GossipStore::new());

        let engagement_manager = crate::engagement::EngagementManager::new(
            node_id.clone(),
            docs.clone(),
            gossip.clone(),
            endpoint.clone(),
        );

        let queue = Arc::new(SubmissionQueue::new(256));

        let llm_config = crate::llm::LlmConfig::default();
        let processor = Arc::new(Processor::new(
            queue.clone(),
            docs,
            llm_config,
            ProcessorMode::Autonomous,
            1,
        ));
        let processor_clone = processor.clone();
        tokio::spawn(async move { processor_clone.start().await });

        Ok(Self {
            identity_manager,
            engagement_manager,
            processor,
            endpoint,
            p2p_node_id,
            p2p_relay_urls,
            p2p_direct_addrs,
        })
    }

    pub async fn new_with_processor(
        _queue: Arc<SubmissionQueue>,
        processor: Arc<Processor>,
    ) -> anyhow::Result<Self> {
        let identity_manager = crate::identity::IdentityManager::load_or_create().await?;
        let node_id = identity_manager.get_info()?.node_id;

        // Create iroh endpoint for P2P networking
        let endpoint = Arc::new(
            iroh::endpoint::Endpoint::builder(iroh::endpoint::presets::Minimal)
                .bind()
                .await?,
        );
        tracing::info!("Endpoint bound: {}", endpoint.id());

        let docs = Arc::new(crate::docs::DocsStore::new());
        let gossip = Arc::new(crate::gossip::GossipStore::new());

        let engagement_manager = crate::engagement::EngagementManager::new(
            node_id,
            docs,
            gossip,
            endpoint.clone(),
        );

        Ok(Self {
            identity_manager,
            engagement_manager,
            processor,
            endpoint,
            p2p_node_id: String::new(),
            p2p_relay_urls: Vec::new(),
            p2p_direct_addrs: Vec::new(),
        })
    }

    /// Access the submission queue (for P2P handler to enqueue directly).
    pub fn submission_queue(&self) -> Arc<SubmissionQueue> {
        self.processor.queue.clone()
    }

    /// Access the doc store (for P2P handler to serve state queries).
    pub fn doc_store(&self) -> Arc<crate::docs::DocsStore> {
        self.engagement_manager.docs.clone()
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
            "get_node_addr" => self.get_node_addr(request.id).await,
            "ping" => Response::Success {
                id: request.id,
                result: json!({"pong": true}),
            },
            "get_queue_stats" => self.get_queue_stats(request.id, request.params).await,
            "get_review_queue" => self.get_review_queue(request.id, request.params).await,
            "set_processor_mode" => self.set_processor_mode(request.id, request.params).await,
            "approve_decision" => self.approve_decision(request.id, request.params).await,
            "reject_decision" => self.reject_decision(request.id, request.params).await,
            "override_severity" => self.override_severity(request.id, request.params).await,
            "p2p_dial" => self.p2p_dial(request.id, request.params).await,
            "p2p_submit_remote" => self.p2p_submit_remote(request.id, request.params).await,
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
            Ok(info) => Response::Success {
                id,
                result: json!(info),
            },
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "IDENTITY_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn create_engagement(&self, id: u64, params: Value) -> Response {
        let name = match params.get("name").and_then(|v| v.as_str()) {
            Some(n) => n.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'name'".into(),
                    },
                }
            }
        };

        match self.engagement_manager.create(name).await {
            Ok(meta) => {
                let result = match serde_json::to_value(&meta) {
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
                error: ResponseError {
                    code: "ENGAGEMENT_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn generate_invite(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'engagement_id'".into(),
                    },
                }
            }
        };

        match self.engagement_manager.generate_invite(&engagement_id).await {
            Ok(invite) => Response::Success {
                id,
                result: json!({"invite": invite}),
            },
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "INVITE_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn join_engagement(&self, id: u64, params: Value) -> Response {
        let invite = match params.get("invite").and_then(|v| v.as_str()) {
            Some(i) => i.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'invite'".into(),
                    },
                }
            }
        };

        match self.engagement_manager.join(&invite).await {
            Ok(meta) => {
                let result = match serde_json::to_value(&meta) {
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
                error: ResponseError {
                    code: "JOIN_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn leave_engagement(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'engagement_id'".into(),
                    },
                }
            }
        };

        match self.engagement_manager.leave(&engagement_id).await {
            Ok(()) => Response::Success {
                id,
                result: json!({"left": true}),
            },
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "LEAVE_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn submit(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'engagement_id'".into(),
                    },
                }
            }
        };

        let submission_data = match params.get("submission").and_then(|s| s.get("data")) {
            Some(s) => s.clone(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'submission.data'".into(),
                    },
                }
            }
        };

        let sub = Submission {
            submission_id: uuid::Uuid::new_v4().to_string(),
            engagement_id: engagement_id.clone(),
            author_node_id: self
                .identity_manager
                .get_info()
                .map(|i| i.node_id)
                .unwrap_or_default(),
            data: submission_data,
        };
        let submission_id = sub.submission_id.clone();
        self.processor
            .queue
            .enqueue(sub)
            .await
            .map_err(|e| Response::Error {
                id,
                error: ResponseError {
                    code: "QUEUE_FULL".into(),
                    message: e.to_string(),
                },
            })
            .map(|_| Response::Success {
                id,
                result: json!({"submission_id": submission_id}),
            })
            .unwrap_or_else(|e| e)
    }

    async fn get_state(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'engagement_id'".into(),
                    },
                }
            }
        };

        match self.engagement_manager.get_state(&engagement_id).await {
            Ok(state) => Response::Success { id, result: state },
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "STATE_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn add_blob(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'engagement_id'".into(),
                    },
                }
            }
        };

        let data_b64 = match params.get("data").and_then(|v| v.as_str()) {
            Some(d) => d,
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'data'".into(),
                    },
                }
            }
        };

        use base64::Engine;
        let data = match base64::engine::general_purpose::STANDARD.decode(data_b64) {
            Ok(d) => d,
            Err(e) => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "BASE64_ERROR".into(),
                        message: e.to_string(),
                    },
                }
            }
        };

        match crate::blobs::add_blob(&engagement_id, &data).await {
            Ok(hash) => Response::Success {
                id,
                result: json!({"hash": hash}),
            },
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "BLOB_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn get_blob(&self, id: u64, params: Value) -> Response {
        let engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'engagement_id'".into(),
                    },
                }
            }
        };

        let hash = match params.get("hash").and_then(|v| v.as_str()) {
            Some(h) => h.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'hash'".into(),
                    },
                }
            }
        };

        match crate::blobs::get_blob(&engagement_id, &hash).await {
            Ok(data) => {
                use base64::Engine;
                let b64 = base64::engine::general_purpose::STANDARD.encode(&data);
                Response::Success {
                    id,
                    result: json!({"data": b64}),
                }
            }
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "BLOB_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn get_node_addr(&self, id: u64) -> Response {
        Response::Success {
            id,
            result: json!({
                "node_id": self.p2p_node_id,
                "relay_urls": self.p2p_relay_urls,
                "direct_addresses": self.p2p_direct_addrs,
            }),
        }
    }

    async fn get_queue_stats(&self, id: u64, params: Value) -> Response {
        let _engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'engagement_id'".into(),
                    },
                }
            }
        };
        let stats = self.processor.stats().await;
        Response::Success {
            id,
            result: serde_json::to_value(stats).unwrap_or_default(),
        }
    }

    async fn get_review_queue(&self, id: u64, params: Value) -> Response {
        let _engagement_id = match params.get("engagement_id").and_then(|v| v.as_str()) {
            Some(eid) => eid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'engagement_id'".into(),
                    },
                }
            }
        };
        let queue = self.processor.get_review_queue().await;
        Response::Success {
            id,
            result: serde_json::to_value(queue).unwrap_or_default(),
        }
    }

    async fn set_processor_mode(&self, id: u64, params: Value) -> Response {
        let mode_str = match params.get("mode").and_then(|v| v.as_str()) {
            Some(m) => m,
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'mode'".into(),
                    },
                }
            }
        };
        let mode = match mode_str {
            "autonomous" => ProcessorMode::Autonomous,
            "review" => ProcessorMode::ReviewRequired,
            "critical" => ProcessorMode::CriticalOnly,
            _ => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: format!("Unknown mode: {}", mode_str),
                    },
                }
            }
        };
        self.processor.set_mode(mode).await;
        Response::Success {
            id,
            result: json!({"mode": mode_str}),
        }
    }

    async fn approve_decision(&self, id: u64, params: Value) -> Response {
        let submission_id = match params.get("submission_id").and_then(|v| v.as_str()) {
            Some(sid) => sid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'submission_id'".into(),
                    },
                }
            }
        };
        match self.processor.approve_decision(&submission_id).await {
            Ok(()) => Response::Success {
                id,
                result: json!({"approved": submission_id}),
            },
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "APPROVE_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn reject_decision(&self, id: u64, params: Value) -> Response {
        let submission_id = match params.get("submission_id").and_then(|v| v.as_str()) {
            Some(sid) => sid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'submission_id'".into(),
                    },
                }
            }
        };
        let reason = params
            .get("reason")
            .and_then(|v| v.as_str())
            .unwrap_or("rejected_by_operator");
        self.processor
            .reject_decision(&submission_id, reason)
            .await;
        Response::Success {
            id,
            result: json!({"rejected": submission_id}),
        }
    }

    async fn override_severity(&self, id: u64, params: Value) -> Response {
        let submission_id = match params.get("submission_id").and_then(|v| v.as_str()) {
            Some(sid) => sid.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'submission_id'".into(),
                    },
                }
            }
        };
        let severity = match params.get("severity").and_then(|v| v.as_str()) {
            Some(s) => s.to_string(),
            None => {
                return Response::Error {
                    id,
                    error: ResponseError {
                        code: "INVALID_PARAMS".into(),
                        message: "Missing 'severity'".into(),
                    },
                }
            }
        };
        match self
            .processor
            .override_severity(&submission_id, &severity)
            .await
        {
            Ok(()) => Response::Success {
                id,
                result: json!({"overridden": submission_id, "severity": severity}),
            },
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "OVERRIDE_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn p2p_dial(&self, id: u64, params: Value) -> Response {
        let node_id_str = match params.get("node_id").and_then(|v| v.as_str()) {
            Some(n) => n,
            None => {
                return Response::Error {
                    id,
                    error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'node_id'".into() },
                }
            }
        };
        let node_id: iroh::EndpointId = match node_id_str.parse() {
            Ok(n) => n,
            Err(e) => {
                return Response::Error {
                    id,
                    error: ResponseError { code: "INVALID_NODE_ID".into(), message: format!("Invalid node_id: {}", e) },
                }
            }
        };

        // Parse optional addresses
        let addrs: Vec<iroh::TransportAddr> = params
            .get("addresses")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter().filter_map(|a| {
                    a.as_str().and_then(|s| s.parse::<std::net::SocketAddr>().ok().map(|sa| iroh::TransportAddr::Ip(sa)))
                }).collect()
            })
            .unwrap_or_default();

        match crate::p2p::dial(&self.endpoint, node_id, addrs, None).await {
            Ok(mut stream) => {
                // Send a ping and get response
                let msg = json!({"method": "ping", "params": {}});
                if let Err(e) = stream.send_json(&msg).await {
                    return Response::Error {
                        id,
                        error: ResponseError {
                            code: "P2P_ERROR".into(),
                            message: format!("Send failed: {}", e),
                        },
                    };
                }
                match stream.recv_json().await {
                    Ok(resp) => Response::Success {
                        id,
                        result: json!({"remote_response": resp}),
                    },
                    Err(e) => Response::Error {
                        id,
                        error: ResponseError {
                            code: "P2P_ERROR".into(),
                            message: format!("Recv failed: {}", e),
                        },
                    },
                }
            }
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "P2P_CONNECT_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }

    async fn p2p_submit_remote(&self, id: u64, params: Value) -> Response {
        let node_id_str = match params.get("node_id").and_then(|v| v.as_str()) {
            Some(n) => n,
            None => {
                return Response::Error { id, error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'node_id'".into() } }
            }
        };
        let node_id: iroh::EndpointId = match node_id_str.parse() {
            Ok(n) => n,
            Err(e) => {
                return Response::Error { id, error: ResponseError { code: "INVALID_NODE_ID".into(), message: format!("Invalid node_id: {}", e) } }
            }
        };
        let engagement_id = params.get("engagement_id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        let submission = match params.get("submission") {
            Some(s) => s.clone(),
            None => {
                return Response::Error { id, error: ResponseError { code: "INVALID_PARAMS".into(), message: "Missing 'submission'".into() } }
            }
        };
        let addrs: Vec<iroh::TransportAddr> = params.get("addresses").and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|a| a.as_str().and_then(|s| s.parse::<std::net::SocketAddr>().ok().map(|sa| iroh::TransportAddr::Ip(sa)))).collect())
            .unwrap_or_default();
        tracing::info!("p2p_submit_remote: parsed {} addresses from params", addrs.len());

        match crate::p2p::dial(&self.endpoint, node_id, addrs, None).await {
            Ok(mut stream) => {
                let msg = json!({"method": "submit", "params": {"engagement_id": engagement_id, "submission": submission}});
                if let Err(e) = stream.send_json(&msg).await {
                    return Response::Error {
                        id,
                        error: ResponseError {
                            code: "P2P_ERROR".into(),
                            message: format!("Send failed: {}", e),
                        },
                    };
                }
                match stream.recv_json().await {
                    Ok(resp) => Response::Success {
                        id,
                        result: json!({"remote_response": resp}),
                    },
                    Err(e) => Response::Error {
                        id,
                        error: ResponseError {
                            code: "P2P_ERROR".into(),
                            message: format!("Recv failed: {}", e),
                        },
                    },
                }
            }
            Err(e) => Response::Error {
                id,
                error: ResponseError {
                    code: "P2P_CONNECT_ERROR".into(),
                    message: e.to_string(),
                },
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    fn parse_addrs(params: &serde_json::Value) -> Vec<iroh::TransportAddr> {
        params
            .get("addresses")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|a| {
                        a.as_str().and_then(|s| {
                            s.parse::<std::net::SocketAddr>()
                                .ok()
                                .map(|sa| iroh::TransportAddr::Ip(sa))
                        })
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    #[test]
    fn test_parse_addresses_valid() {
        let params = json!({
            "addresses": ["100.78.171.49:57312", "172.17.0.1:57312"]
        });
        let addrs = parse_addrs(&params);
        assert_eq!(addrs.len(), 2);
    }

    #[test]
    fn test_parse_addresses_empty_array() {
        let params = json!({ "addresses": [] });
        let addrs = parse_addrs(&params);
        assert_eq!(addrs.len(), 0);
    }

    #[test]
    fn test_parse_addresses_missing_key() {
        let params = json!({});
        let addrs = parse_addrs(&params);
        assert_eq!(addrs.len(), 0);
    }

    #[test]
    fn test_parse_addresses_not_an_array() {
        let params = json!({ "addresses": "not-an-array" });
        let addrs = parse_addrs(&params);
        assert_eq!(addrs.len(), 0);
    }

    #[test]
    fn test_parse_addresses_mixed_valid_invalid() {
        let params = json!({
            "addresses": ["100.78.171.49:57312", "not-an-addr", "172.17.0.1:57312", null, "999.999.999.999:99999"]
        });
        let addrs = parse_addrs(&params);
        assert_eq!(addrs.len(), 2, "only the two valid SocketAddrs should be parsed");
    }

    #[test]
    fn test_parse_localhost() {
        let params = json!({ "addresses": ["127.0.0.1:8080"] });
        let addrs = parse_addrs(&params);
        assert_eq!(addrs.len(), 1);
    }

    #[test]
    fn test_parse_ipv6() {
        let params = json!({ "addresses": ["::1:8080", "[::1]:8080"] });
        let addrs = parse_addrs(&params);
        assert_eq!(addrs.len(), 1, "only [::1]:8080 is a valid SocketAddr literal");
    }
}
