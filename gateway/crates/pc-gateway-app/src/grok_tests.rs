// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
// Behavior cases adapted from Magpie (MIT); see LICENSES/oauth.md and fixture provenance.

use super::*;
use axum::{routing::post, Router};
use pc_gateway_core::{DegradePolicy, Executor, HttpGateClient};
use pc_gateway_evidence::NoopEvidenceSink;
use pc_gateway_protocol::{GatewayRenderer, SubscriptionProtocol, SubscriptionRenderer};
use pc_gateway_transport::ReqwestTransport;
use serde_json::{json, Value};
use std::{num::NonZeroU32, sync::Mutex, time::Duration};

#[derive(Default)]
struct Peers {
    reply: Mutex<Value>,
    sent: Mutex<Vec<Bytes>>,
    checks: Mutex<Vec<Value>>,
}

#[tokio::test]
async fn magpie_tool_selection_and_identity_survive_checks_and_client_json_or_sse() {
    let fixture: Value = serde_json::from_str(include_str!(
        "../tests/fixtures/subscriptions/magpie-gateway.json"
    ))
    .unwrap();
    let peers = Arc::new(Peers::default());
    let router = Router::new()
        .route(
            "/v1/responses",
            post(|State(peers): State<Arc<Peers>>, body: Bytes| async move {
                peers.sent.lock().unwrap().push(body);
                Json(peers.reply.lock().unwrap().clone())
            }),
        )
        .route(
            "/v1/context/check",
            post(
                |State(peers): State<Arc<Peers>>, Json(request): Json<Value>| async move {
                    let phase = request["phase"].clone();
                    peers.checks.lock().unwrap().push(request);
                    Json(json!({"schema":"powercontext.context-check.v1", "phase":phase,
                "decision":"allow", "reason_codes":[],
                "coverage":{"profile":"text-and-tools.v1","complete":true},
                "policy_version":"test", "valid_until":"2999-01-01T00:00:00Z", "binding":"test"
            })).into_response()
                },
            ),
        )
        .with_state(peers.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    // Port zero cannot host Core; its connection failure exercises permitted degradation.
    let executors = [base.as_str(), "http://127.0.0.1:0"].map(|core| {
        Executor::new(
            HttpGateClient::with_token(core, None),
            ReqwestTransport::new(Duration::from_secs(10)),
            NonZeroU32::new(1).unwrap(),
        )
        .with_core_enforcement(false)
    });
    let renderer = SubscriptionRenderer {
        inner: GatewayRenderer::Responses(Default::default()),
        subscription: Some(SubscriptionProtocol::Grok),
    };
    for case in fixture["cases"].as_array().unwrap() {
        for (stream, degraded) in [(false, false), (true, false), (true, true)] {
            // One degraded namespace case covers the distinct bypass path.
            if degraded && case["id"] != "namespaced-calls" {
                continue;
            }
            peers.sent.lock().unwrap().clear();
            peers.checks.lock().unwrap().clear();
            *peers.reply.lock().unwrap() = case["response"].clone();
            let mut request = case["request"].clone();
            request["stream"] = json!(stream);
            let canonical =
                to_canonical_responses(serde_json::from_value(request).unwrap()).unwrap();
            let call = ManagedCall {
                scope_id: "test".into(),
                target_profile: "test".into(),
                request: canonical,
                target_url: format!("{base}/v1/responses"),
                headers: vec![],
                degrade_policy: if degraded {
                    DegradePolicy::PassthroughWithoutMemory
                } else {
                    DegradePolicy::Pause
                },
                inject_context: false,
                include_code: false,
                conversation_id: None,
                sediment: false,
                prepared_contexts: vec![],
            };
            let outcome = executors[usize::from(degraded)]
                .run(
                    RequestControl::new(
                        tokio::time::Instant::now() + Duration::from_secs(10),
                        None,
                    ),
                    call,
                    &renderer,
                    &NoopEvidenceSink,
                )
                .await;
            let response = if stream {
                respond_responses_sse(outcome)
            } else {
                respond(outcome, OutputProtocol::OpenAiResponses)
            };
            let status = response.status();
            if degraded && status == StatusCode::OK {
                assert_eq!(
                    response.headers()["x-pc-degraded"],
                    "passthrough_without_memory"
                );
            }
            let body = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap();
            if case["reject"] == true {
                assert_eq!(status, StatusCode::FORBIDDEN, "{body:?}");
                assert!(peers.sent.lock().unwrap().is_empty());
                continue;
            }
            assert_eq!(
                status,
                StatusCode::OK,
                "{} / {stream} / {degraded}: {body:?}",
                case["id"]
            );
            let client: Value = if stream {
                let mut framer = pc_gateway_protocol::SseFramer::default();
                framer.push(&body);
                let events: Vec<Value> = framer
                    .take_events()
                    .iter()
                    .map(|event| serde_json::from_slice(&event.data).unwrap())
                    .collect();
                let calls: Vec<&Value> = events
                    .iter()
                    .filter(|event| event["item"]["type"] == "function_call")
                    .collect();
                assert_eq!(calls.len(), 2);
                for event in &events {
                    if event["item"]["type"] == "function_call" {
                        for field in ["name", "namespace", "call_id", "encrypted_function_args"] {
                            assert_eq!(
                                event["item"][field],
                                case["expected_response"]["output"][0][field]
                            );
                        }
                    }
                }
                events.last().unwrap()["response"].clone()
            } else {
                serde_json::from_slice(&body).unwrap()
            };
            assert_eq!(client, case["expected_response"], "{}", case["id"]);
            let sent = peers.sent.lock().unwrap();
            assert_eq!(sent.len(), 1);
            let wire: Value = serde_json::from_slice(&sent[0]).unwrap();
            assert_eq!(wire["tools"], case["upstream_tools"]);
            assert_eq!(wire["tool_choice"], case["upstream_choice"]);
            if case["id"] == "namespaced-calls" {
                assert_eq!(wire["input"][1]["name"], "collaboration__spawn_agent");
                assert!(wire["input"][1].get("namespace").is_none());
                assert_eq!(wire["input"][1]["call_id"], wire["input"][2]["call_id"]);
            }
            if !degraded {
                let checks = peers.checks.lock().unwrap();
                assert_eq!(
                    checks[0]["payload"]["body"].as_str().unwrap().as_bytes(),
                    sent[0].as_ref()
                );
                let checked: Value =
                    serde_json::from_str(checks[1]["payload"]["body"].as_str().unwrap()).unwrap();
                assert_eq!(checked, client);
                if !stream {
                    assert_eq!(
                        checks[1]["payload"]["body"].as_str().unwrap().as_bytes(),
                        body.as_ref()
                    );
                }
            }
        }
    }
    server.abort();
}
