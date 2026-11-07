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

//! Real TCP Core/provider receivers through the public Executor. No paid provider or real Core claim.
use axum::{extract::State, routing::post, Json, Router};
use pc_gateway_core::{
    DegradePolicy, ExecutionState, ExecutionTerminal, Executor, HttpGateClient,
    LifecycleStopReason, ManagedCall, ManagedOutcome, ModelSendState, RequestControl,
};
use pc_gateway_evidence::NoopEvidenceSink;
use pc_gateway_protocol::{
    CanonicalMessage, CanonicalRequest, OpenAiChatRenderer, OutputLimits, Role,
};
use pc_gateway_transport::ReqwestTransport;
use serde_json::{json, Value};
use std::{
    num::NonZeroU32,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::Duration,
};

struct Receiver {
    fault: AtomicUsize,
    sends: AtomicUsize,
    entered: tokio::sync::Notify,
    release: tokio::sync::Notify,
}

async fn check(State(state): State<Arc<Receiver>>, Json(request): Json<Value>) -> Json<Value> {
    let phase = request["phase"].as_str().unwrap();
    let fault = if phase == "before_send" { 2 } else { 4 };
    if state.fault.load(Ordering::SeqCst) == fault {
        state.entered.notify_one();
        state.release.notified().await;
    }
    Json(json!({
        "schema": "powercontext.context-check.v1", "phase": phase,
        "decision": "allow", "reason_codes": [], "coverage": {"profile": "test", "complete": true},
        "policy_version": "test", "valid_until": "2999-01-01T00:00:00Z", "binding": "binding"
    }))
}

fn call(base: &str) -> ManagedCall {
    ManagedCall {
        scope_id: "test".into(),
        target_profile: "test".into(),
        request: CanonicalRequest {
            model: "test".into(),
            messages: vec![CanonicalMessage {
                role: Role::User,
                content: vec![json!("hello")],
                content_null: false,
                tool_calls: None,
                tool_call_id: None,
            }],
            tools: vec![],
            output_limits: OutputLimits {
                max_output_tokens: None,
            },
            stream: false,
            instructions: None,
            native_cache_fields: None,
            response_format: None,
            tool_choice: None,
            store: None,
            token_limit_field: None,
            previous_response_id: None,
            conversation: None,
            passthrough_fields: None,
        },
        target_url: format!("{base}/model"),
        headers: vec![],
        degrade_policy: DegradePolicy::PassthroughWithoutMemory,
        inject_context: true,
        include_code: false,
        conversation_id: None,
        sediment: true,
        prepared_contexts: vec![],
    }
}

#[tokio::test]
async fn actual_receivers_verify_stops_zero_send_and_recovery_at_all_network_stages() {
    let state = Arc::new(Receiver {
        fault: AtomicUsize::new(0),
        sends: AtomicUsize::new(0),
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Notify::new(),
    });
    let router = Router::new()
        .route("/v1/context/check", post(check))
        .route("/v1/context/prepare", post(|State(state): State<Arc<Receiver>>| async move {
            if state.fault.load(Ordering::SeqCst) == 1 {
                state.entered.notify_one(); state.release.notified().await;
            }
            Json(json!({"schema":"powercontext.prepared-context.v1", "status":"empty", "content_bytes":0}))
        }))
        .route("/v1/sources/content", post(|State(state): State<Arc<Receiver>>| async move {
            if state.fault.load(Ordering::SeqCst) == 5 {
                state.entered.notify_one(); state.release.notified().await;
            }
            Json(json!({"captured":true}))
        }))
        .route("/model", post(|State(state): State<Arc<Receiver>>| async move {
            state.sends.fetch_add(1, Ordering::SeqCst);
            if state.fault.load(Ordering::SeqCst) == 3 {
                state.entered.notify_one(); state.release.notified().await;
            }
            Json(json!({"choices":[{"message":{"role":"assistant","content":"ok"}}]}))
        }))
        .with_state(state.clone());
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let (shutdown, stopped) = tokio::sync::oneshot::channel::<()>();
    let server = tokio::spawn(async move {
        axum::serve(listener, router)
            .with_graceful_shutdown(async {
                stopped.await.ok();
            })
            .await
            .unwrap();
    });
    let executor = Executor::new(
        HttpGateClient::with_token(&base, None),
        ReqwestTransport::default(),
        NonZeroU32::new(2).unwrap(),
    );
    let renderer = OpenAiChatRenderer;
    for stage in 1..=5 {
        for explicit in [false, true] {
            state.fault.store(stage, Ordering::SeqCst);
            let before = state.sends.load(Ordering::SeqCst);
            let control = RequestControl::new(
                tokio::time::Instant::now() + Duration::from_millis(120),
                None,
            );
            let observation = control.observation();
            let cancellation = control.cancellation();
            let execute = executor.run(control, call(&base), &renderer, &NoopEvidenceSink);
            let stop = async {
                // A real receiver must see entry into the fault before cancellation.
                tokio::time::timeout(Duration::from_secs(2), state.entered.notified())
                    .await
                    .unwrap();
                if explicit {
                    cancellation.cancel();
                }
            };
            let (outcome, ()) = tokio::join!(execute, stop);
            let reason = if explicit {
                LifecycleStopReason::RequestCancelled
            } else {
                LifecycleStopReason::DeadlineExceeded
            };
            let expected_send = match stage {
                1 | 2 => ModelSendState::NoSend,
                3 => ModelSendState::PossiblySent,
                _ => ModelSendState::ResponseReceived,
            };
            assert!(
                matches!(outcome, ManagedOutcome::Stopped { reason: actual, model_send } if actual == reason && model_send == expected_send),
                "stage {stage}: {outcome:?}"
            );
            assert_eq!(
                observation.snapshot().execution,
                ExecutionState::Finished(ExecutionTerminal::Stopped(reason))
            );
            assert_eq!(observation.snapshot().stop_reason, Some(reason));
            assert_eq!(
                state.sends.load(Ordering::SeqCst) - before,
                usize::from(stage >= 3)
            );
            state.fault.store(0, Ordering::SeqCst);
            state.release.notify_waiters();
            let control =
                RequestControl::new(tokio::time::Instant::now() + Duration::from_secs(2), None);
            assert!(matches!(
                executor
                    .run(control, call(&base), &renderer, &NoopEvidenceSink)
                    .await,
                ManagedOutcome::Released { .. }
            ));
        }
    }
    // The existing 120-second provider attempt limit wins when total work has 180 seconds.
    state.fault.store(3, Ordering::SeqCst);
    let before = state.sends.load(Ordering::SeqCst);
    let control = RequestControl::new(tokio::time::Instant::now() + Duration::from_secs(180), None);
    let observation = control.observation();
    let execute = executor.run(control, call(&base), &renderer, &NoopEvidenceSink);
    let advance = async {
        state.entered.notified().await;
        tokio::time::pause();
        tokio::time::advance(Duration::from_secs(121)).await;
        tokio::time::resume();
    };
    let (outcome, ()) = tokio::join!(execute, advance);
    assert!(matches!(outcome, ManagedOutcome::SendIndeterminate));
    assert_eq!(observation.snapshot().stop_reason, None);
    assert_eq!(
        observation.snapshot().model_send,
        ModelSendState::PossiblySent
    );
    assert_eq!(state.sends.load(Ordering::SeqCst), before + 1);
    state.fault.store(0, Ordering::SeqCst);
    state.release.notify_waiters();
    let control = RequestControl::new(tokio::time::Instant::now() + Duration::from_secs(2), None);
    assert!(matches!(
        executor
            .run(control, call(&base), &renderer, &NoopEvidenceSink)
            .await,
        ManagedOutcome::Released { .. }
    ));
    // Stop waiting remote handlers too; the test never treats remote rollback as a local stop guarantee.
    shutdown.send(()).unwrap();
    tokio::time::timeout(Duration::from_secs(2), server)
        .await
        .unwrap()
        .unwrap();
}

#[tokio::test]
async fn http_core_dripping_framing_stays_within_the_shared_deadline() {
    use pc_gateway_core::GateClient;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    for prefix in [
        &b"HTTP/1.1 200 OK\r\nX-Drip: "[..],
        &b"HTTP/1.1 200 OK\r\nContent-Length: 99999\r\n\r\n"[..],
        &b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\nX-Drip: "[..],
    ] {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                head.push(stream.read_u8().await.unwrap());
            }
            stream.write_all(prefix).await.unwrap();
            while stream.write_all(b"x").await.is_ok() {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                head.push(stream.read_u8().await.unwrap());
            }
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                .await
                .unwrap();
        });
        let client = HttpGateClient::with_token(base, None);
        let control = RequestControl::new(
            tokio::time::Instant::now() + Duration::from_millis(100),
            None,
        );
        let began = tokio::time::Instant::now();
        assert!(matches!(
            control.lifecycle().run(client.capabilities()).await,
            Err(LifecycleStopReason::DeadlineExceeded)
        ));
        assert!(began.elapsed() < Duration::from_secs(2));
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(2), client.capabilities())
                .await
                .unwrap()
                .unwrap(),
            json!({})
        );
        server.await.unwrap();
    }
}

#[tokio::test]
async fn refused_model_connect_requires_fresh_admission_and_obeys_stops() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    for mode in ["success", "cancel", "deadline", "closed", "expired"] {
        let reserved = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let provider_address = reserved.local_addr().unwrap();
        drop(reserved); // First real TCP connect must be refused, before any request bytes.
        let recheck_entered = Arc::new(tokio::sync::Notify::new());
        let release_recheck = Arc::new(tokio::sync::Notify::new());
        let admissions = Arc::new(AtomicUsize::new(0));
        let checks = admissions.clone();
        let entered = recheck_entered.clone();
        let release = release_recheck.clone();
        let router = Router::new().route("/v1/context/check", post(move |Json(request): Json<Value>| {
            let checks = checks.clone(); let entered = entered.clone(); let release = release.clone();
            async move {
                let phase = request["phase"].as_str().unwrap();
                let binding = if phase == "before_send" {
                    let number = checks.fetch_add(1, Ordering::SeqCst) + 1;
                    if number == 2 { entered.notify_one(); release.notified().await; }
                    format!("binding-{number}")
                } else {
                    assert_eq!(request["request_binding"], "binding-2", "release must consume the fresh approval");
                    "released".into()
                };
                Json(json!({"schema":"powercontext.context-check.v1", "phase":phase, "decision":"allow",
                    "reason_codes":[], "coverage":{"profile":"test", "complete":true},
                    "policy_version":"test", "binding":binding,
                    "valid_until": if mode == "expired" && binding == "binding-2" { "2000-01-01T00:00:00Z" } else { "2999-01-01T00:00:00Z" }}))
            }
        }));
        let core = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let core_url = format!("http://{}", core.local_addr().unwrap());
        let server = tokio::spawn(async move {
            axum::serve(core, router).await.unwrap();
        });
        let executor = Executor::new(
            HttpGateClient::with_token(core_url, None),
            ReqwestTransport::default(),
            NonZeroU32::new(2).unwrap(),
        );
        let control = RequestControl::new(
            tokio::time::Instant::now() + Duration::from_millis(250),
            None,
        );
        let cancellation = control.cancellation();
        let observation = control.observation();
        let mut managed = call(&format!("http://{provider_address}"));
        managed.inject_context = false;
        managed.sediment = false;
        let execute = executor.run(control, managed, &OpenAiChatRenderer, &NoopEvidenceSink);
        let supervise = async {
            tokio::time::timeout(Duration::from_secs(2), recheck_entered.notified())
                .await
                .unwrap();
            let provider = match mode {
                "success" => {
                    let listener = tokio::net::TcpListener::bind(provider_address)
                        .await
                        .unwrap();
                    Some(tokio::spawn(async move {
                        let (mut stream, _) = listener.accept().await.unwrap();
                        let mut head = Vec::new();
                        while !head.ends_with(b"\r\n\r\n") {
                            head.push(stream.read_u8().await.unwrap());
                        }
                        let len: usize = String::from_utf8_lossy(&head)
                            .lines()
                            .find_map(|line| {
                                line.to_ascii_lowercase()
                                    .strip_prefix("content-length: ")
                                    .map(str::to_owned)
                            })
                            .unwrap()
                            .parse()
                            .unwrap();
                        let mut body = vec![0; len];
                        stream.read_exact(&mut body).await.unwrap();
                        assert_eq!(
                            serde_json::from_slice::<Value>(&body).unwrap()["model"],
                            "test"
                        );
                        let body =
                            r#"{"choices":[{"message":{"role":"assistant","content":"ok"}}]}"#;
                        stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).await.unwrap();
                        assert!(tokio::time::timeout(
                            Duration::from_millis(100),
                            listener.accept()
                        )
                        .await
                        .is_err());
                    }))
                }
                "cancel" => {
                    cancellation.cancel();
                    None
                }
                "closed" => {
                    executor.close_model_admission();
                    None
                }
                _ => None,
            };
            if mode != "deadline" {
                release_recheck.notify_one();
            }
            provider
        };
        let (outcome, provider) = tokio::join!(execute, supervise);
        match mode {
            "success" => assert!(
                matches!(outcome, ManagedOutcome::Released { .. }),
                "{outcome:?}"
            ),
            "expired" => assert!(matches!(outcome, ManagedOutcome::AdmissionExpired)),
            _ => {
                let reason = match mode {
                    "cancel" => LifecycleStopReason::RequestCancelled,
                    "closed" => LifecycleStopReason::GatewayShutdown,
                    _ => LifecycleStopReason::DeadlineExceeded,
                };
                assert!(
                    matches!(outcome, ManagedOutcome::Stopped { reason: actual, model_send: ModelSendState::DefinitelyNotSent } if actual == reason),
                    "{mode}: {outcome:?}"
                );
                assert_eq!(observation.snapshot().stop_reason, Some(reason));
            }
        }
        assert_eq!(admissions.load(Ordering::SeqCst), 2);
        if let Some(provider) = provider {
            provider.await.unwrap();
        }
        server.abort();
        let _ = server.await;
    }
}

#[tokio::test]
async fn production_executor_never_replays_a_complete_request_after_provider_disconnect() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let state = Arc::new(Receiver {
        fault: AtomicUsize::new(0),
        sends: AtomicUsize::new(0),
        entered: tokio::sync::Notify::new(),
        release: tokio::sync::Notify::new(),
    });
    let core = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let core_url = format!("http://{}", core.local_addr().unwrap());
    let router = Router::new()
        .route("/v1/context/check", post(check))
        .with_state(state);
    let core_server = tokio::spawn(async move {
        axum::serve(core, router).await.unwrap();
    });
    for partial_response in [false, true] {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let provider = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                head.push(stream.read_u8().await.unwrap());
            }
            let length: usize = String::from_utf8_lossy(&head)
                .lines()
                .find_map(|line| {
                    line.to_ascii_lowercase()
                        .strip_prefix("content-length: ")
                        .map(str::to_owned)
                })
                .unwrap()
                .parse()
                .unwrap();
            let mut body = vec![0; length];
            stream.read_exact(&mut body).await.unwrap();
            assert_eq!(
                serde_json::from_slice::<Value>(&body).unwrap()["model"],
                "test"
            );
            if partial_response {
                stream
                    .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 999\r\n\r\n{\"choices\":")
                    .await
                    .unwrap();
            }
            stream.shutdown().await.unwrap();
            drop(stream);
            assert!(
                tokio::time::timeout(Duration::from_millis(150), listener.accept())
                    .await
                    .is_err(),
                "uncertain paid request must not be resent"
            );
        });
        let executor = Executor::new(
            HttpGateClient::with_token(&core_url, None),
            ReqwestTransport::default(),
            NonZeroU32::new(3).unwrap(),
        );
        let control =
            RequestControl::new(tokio::time::Instant::now() + Duration::from_secs(2), None);
        let observation = control.observation();
        let mut managed = call(&base);
        managed.inject_context = false;
        managed.sediment = false;
        assert!(matches!(
            executor
                .run(control, managed, &OpenAiChatRenderer, &NoopEvidenceSink)
                .await,
            ManagedOutcome::SendIndeterminate
        ));
        assert_eq!(
            observation.snapshot().model_send,
            ModelSendState::PossiblySent
        );
        provider.await.unwrap();
    }
    core_server.abort();
    let _ = core_server.await;
}
