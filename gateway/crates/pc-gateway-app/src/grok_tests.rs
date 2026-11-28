// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
// Behavior cases adapted from Magpie (MIT); see LICENSES/oauth.md and fixture provenance.

use super::*;
use axum::{routing::post, Router};
use pc_gateway_core::{Executor, HttpGateClient};
use pc_gateway_evidence::NoopEvidenceSink;
use pc_gateway_protocol::{GatewayRenderer, SubscriptionProtocol, SubscriptionRenderer};
use pc_gateway_transport::ReqwestTransport;
use serde_json::{json, Value};
use std::{num::NonZeroU32, sync::Mutex, time::Duration};

#[derive(Default)]
struct Peers {
    reply: Mutex<Bytes>,
    reply_error: Mutex<Option<StatusCode>>,
    block_body: Mutex<Option<Bytes>>,
    target: Mutex<String>,
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
                if let Some(status) = *peers.reply_error.lock().unwrap() {
                    (status, [(header::CONTENT_TYPE, "text/plain")], "upstream error").into_response()
                } else {
                    ([(header::CONTENT_TYPE, "application/json")], peers.reply.lock().unwrap().clone()).into_response()
                }
            }),
        )
        .route(
            "/v1/context/check",
            post(
                |State(peers): State<Arc<Peers>>, Json(request): Json<Value>| async move {
                    let phase = request["phase"].clone();
                    let blocked = phase == "before_release" && peers.block_body.lock().unwrap().as_ref().is_some_and(|body| {
                        request["payload"]["body"].as_str().is_some_and(|text| text.as_bytes() == body.as_ref())
                    });
                    peers.checks.lock().unwrap().push(request);
                    Json(json!({"schema":"powercontext.context-check.v1", "phase":phase,
                "decision":if blocked {"block"} else {"allow"}, "reason_codes":if blocked {vec!["forbidden_content"]} else {vec![]},
                "coverage":{"profile":"text-and-tools.v1","complete":true},
                "policy_version":"test", "valid_until":"2999-01-01T00:00:00Z", "binding":"test",
                "target_endpoint": if phase == "before_send" { Some(peers.target.lock().unwrap().clone()) } else { None },
                "target_protocol": "openai-responses"
            })).into_response()
                },
            ),
        )
        .with_state(peers.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    *peers.target.lock().unwrap() = format!("{base}/v1/responses");
    let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
    let executor = Executor::new(
        HttpGateClient::with_token(&base, None),
        ReqwestTransport::new(Duration::from_secs(10)),
        NonZeroU32::new(1).unwrap(),
    )
    .with_core_enforcement(false);
    let renderer = SubscriptionRenderer {
        inner: GatewayRenderer::Responses(Default::default()),
        subscription: Some(SubscriptionProtocol::Grok),
    };
    for case in fixture["cases"].as_array().unwrap() {
        for (stream, upstream_error) in [
            (false, None),
            (true, None),
            (false, Some(StatusCode::TOO_MANY_REQUESTS)),
            (true, Some(StatusCode::BAD_GATEWAY)),
        ] {
            if upstream_error.is_some() && case["id"] != "namespaced-calls" {
                continue;
            }
            *peers.reply_error.lock().unwrap() = upstream_error;
            peers.sent.lock().unwrap().clear();
            peers.checks.lock().unwrap().clear();
            *peers.reply.lock().unwrap() = serde_json::to_vec(&case["response"]).unwrap().into();
            let mut request = case["request"].clone();
            request["stream"] = json!(stream);
            let canonical =
                to_canonical_responses(serde_json::from_value(request).unwrap()).unwrap();
            let call = ManagedCall {
                scope_id: "test".into(),
                target_profile: "test".into(),
                request: canonical,
                target_protocol: "openai-responses".into(),
                credential_target: None,
                headers: vec![],
                inject_context: false,
                include_code: false,
                capture_prefix: false,
                conversation_id: None,
                sediment: false,
                internal: false,
                prepared_contexts: vec![],
            };
            let outcome = executor
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
            if let Some(expected) = upstream_error {
                assert_eq!(status, expected);
                assert_eq!(response.headers()[header::CONTENT_TYPE], "text/plain");
                let body = axum::body::to_bytes(response.into_body(), usize::MAX)
                    .await
                    .unwrap();
                assert_eq!(body.as_ref(), b"upstream error");
                assert_eq!(peers.sent.lock().unwrap().len(), 1);
                continue;
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
                "{} / {stream}: {body:?}",
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
            {
                let checks = peers.checks.lock().unwrap();
                assert_eq!(
                    checks[0]["payload"]["body"].as_str().unwrap().as_bytes(),
                    sent[0].as_ref()
                );
                assert_eq!(
                    checks[1]["payload"]["body"].as_str().unwrap().as_bytes(),
                    peers.reply.lock().unwrap().as_ref()
                );
                let final_check = checks.last().unwrap();
                let checked: Value =
                    serde_json::from_str(final_check["payload"]["body"].as_str().unwrap()).unwrap();
                assert_eq!(checked, client);
                if !stream {
                    assert_eq!(
                        final_check["payload"]["body"].as_str().unwrap().as_bytes(),
                        body.as_ref()
                    );
                }
            }
        }
    }
    *peers.reply_error.lock().unwrap() = None;
    // Raw spellings and flattened names remain evidence even if normalization changes them.
    let raw = br#"{"id":"r","object":"response","status":"completed","output":[{"type":"function_call","name":"collaboration__spawn_agent","call_id":"c9","arguments":"{}"}],"metadata":{"number":1e+03}}"#;
    let duplicate_arguments = br#"{"id":"r","object":"response","status":"completed","output":[{"type":"function_call","name":"collaboration__spawn_agent","call_id":"c9","arguments":"{\"secret\":\"synthetic-review-forbidden\"}","arguments":"{}"}]}"#;
    let duplicate_metadata = br#"{"id":"r","object":"response","status":"completed","output":[{"type":"function_call","name":"collaboration__spawn_agent","call_id":"c9","arguments":"{}"}],"metadata":[{"value":"synthetic-review-forbidden","va\u006cue":"safe"}]}"#;
    for (reply, block_original) in [
        (raw.as_slice(), true),
        (duplicate_arguments.as_slice(), false),
        (duplicate_metadata.as_slice(), false),
    ] {
        {
            peers.sent.lock().unwrap().clear();
            peers.checks.lock().unwrap().clear();
            *peers.reply.lock().unwrap() = Bytes::copy_from_slice(reply);
            *peers.block_body.lock().unwrap() =
                block_original.then(|| Bytes::copy_from_slice(reply));
            let request = fixture["cases"][0]["request"].clone();
            let call = ManagedCall {
                scope_id: "test".into(),
                target_profile: "test".into(),
                request: to_canonical_responses(serde_json::from_value(request).unwrap()).unwrap(),
                target_protocol: "openai-responses".into(),
                credential_target: None,
                headers: vec![],
                inject_context: false,
                include_code: false,
                capture_prefix: false,
                conversation_id: None,
                sediment: false,
                internal: false,
                prepared_contexts: vec![],
            };
            let outcome = executor
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
            let response = respond_responses_sse(outcome);
            assert_eq!(
                response.status(),
                if block_original {
                    StatusCode::FORBIDDEN
                } else {
                    StatusCode::BAD_REQUEST
                }
            );
            let body = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap();
            assert!(!String::from_utf8_lossy(&body).contains("synthetic-review-forbidden"));
            assert_eq!(peers.sent.lock().unwrap().len(), 1);
            if block_original {
                let checks = peers.checks.lock().unwrap();
                assert!(checks.iter().any(|check| check["phase"] == "before_release"
                    && check["payload"]["body"]
                        .as_str()
                        .is_some_and(|body| body.as_bytes() == reply)));
            }
        }
    }
    server.abort();
}
