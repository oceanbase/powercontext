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

//! Spawn the actual gateway against controlled Core/provider HTTP receivers.
//! These tests establish gateway behavior, not real Core/provider acceptance.
use axum::{
    body::Body,
    extract::State,
    http::StatusCode,
    routing::{get, post},
    Json, Router,
};
use bytes::Bytes;
use pc_gateway_types::CONTEXT_CHECK_BODY_LIMIT;
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    convert::Infallible,
    pin::Pin,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Mutex,
    },
    task::{Context, Poll},
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
};

fn capabilities(limit: Value) -> Value {
    json!({"context_protection": {
        "check_versions": ["powercontext.context-check.v1"],
        "payload_versions": ["powercontext.model-json-payload.v1"],
        "reuse_versions": [], "reuse_families": [], "enforcing": true,
        "limits": {"payload_bytes": limit, "binding_bytes": 8192, "prepared_contexts": 16,
            "dependencies": 256, "send_seconds": 60, "correlate_seconds": 300}
    }})
}

struct Receiver {
    caps: Value,
    check_limit: AtomicUsize,
    sends: AtomicUsize,
    checked: Mutex<Vec<String>>,
    response_bytes: AtomicUsize,
    chunked_response: bool,
    fallback_seen: Mutex<Vec<(usize, bool)>>,
}

async fn check(
    State(state): State<Arc<Receiver>>,
    Json(request): Json<Value>,
) -> (StatusCode, Json<Value>) {
    let body = request["payload"]["body"].as_str().unwrap();
    if request["phase"] == "before_send" {
        state.checked.lock().unwrap().push(body.into());
        if body.len() > state.check_limit.load(Ordering::SeqCst) {
            return (
                StatusCode::PAYLOAD_TOO_LARGE,
                Json(json!({"error": "context-check-too-large"})),
            );
        }
    }
    (
        StatusCode::OK,
        Json(json!({
            "schema": "powercontext.context-check.v1", "phase": request["phase"],
            "decision": "allow", "reason_codes": [], "coverage": {"profile": "test", "complete": true},
            "policy_version": "test", "valid_until": "2999-01-01T00:00:00Z", "binding": "test-binding"
        })),
    )
}

struct Chunks(VecDeque<Bytes>);
impl http_body::Body for Chunks {
    type Data = Bytes;
    type Error = Infallible;
    fn poll_frame(
        mut self: Pin<&mut Self>,
        _: &mut Context<'_>,
    ) -> Poll<Option<Result<http_body::Frame<Bytes>, Infallible>>> {
        Poll::Ready(self.0.pop_front().map(|b| Ok(http_body::Frame::data(b))))
    }
}

async fn provider(State(state): State<Arc<Receiver>>, body: Bytes) -> axum::response::Response {
    state.sends.fetch_add(1, Ordering::SeqCst);
    assert!(state
        .checked
        .lock()
        .unwrap()
        .iter()
        .any(|checked| checked.as_bytes() == body));
    let size = state.response_bytes.load(Ordering::SeqCst);
    let body = if size == 0 {
        b"{\"ok\":true}".to_vec()
    } else {
        vec![b'x'; size]
    };
    let body = if state.chunked_response {
        Body::new(Chunks(
            body.chunks(4096).map(Bytes::copy_from_slice).collect(),
        ))
    } else {
        Body::from(body)
    };
    axum::response::Response::builder()
        .header("content-type", "application/json")
        .body(body)
        .unwrap()
}

struct Gateway {
    process: Child,
    server: tokio::task::JoinHandle<()>,
    state: Arc<Receiver>,
    base: String,
    steering: Option<std::path::PathBuf>,
    uds: Option<(tokio::task::JoinHandle<()>, std::path::PathBuf)>,
}
impl Drop for Gateway {
    fn drop(&mut self) {
        let _ = self.process.kill();
        let _ = self.process.wait();
        self.server.abort();
        if let Some((task, path)) = &self.uds {
            task.abort();
            let _ = std::fs::remove_file(path);
        }
        if let Some(path) = &self.steering {
            let _ = std::fs::remove_file(path);
        }
    }
}
impl Gateway {
    async fn start(
        caps: Value,
        deployment_limit: usize,
        injection: bool,
        steering: bool,
        chunked_response: bool,
        combined: bool,
    ) -> Self {
        let state = Arc::new(Receiver {
            caps,
            check_limit: AtomicUsize::new(CONTEXT_CHECK_BODY_LIMIT),
            sends: AtomicUsize::new(0),
            checked: Mutex::new(vec![]),
            response_bytes: AtomicUsize::new(0),
            chunked_response,
            fallback_seen: Mutex::new(vec![]),
        });
        let app = Router::new()
            .route("/v1/capabilities", get(|State(s): State<Arc<Receiver>>| async move { (if s.caps.is_null() { StatusCode::SERVICE_UNAVAILABLE } else { StatusCode::OK }, Json(s.caps.clone())) }))
            .route("/v1/context/check", post(check))
            .route("/v1/context/prepare", post(|| async { Json(json!({
                "schema": "powercontext.prepared-context-with-reuse.v1",
                "context": {"schema": "powercontext.prepared-context.v1", "status": "ready",
                    "content": "准".repeat(128), "content_bytes": 384},
                "reuse": {"schema": "powercontext.context-reuse.v1", "expires_at": "2999-01-01T00:00:00Z", "binding": "reuse"}
            })) }))
            .route("/echo", post(echo))
            .route("/sse", get(|| async { axum::response::Response::builder()
                .header("content-type", "text/event-stream").body(Body::new(OpenSse(false))).unwrap() }))
            .route("/provider", post(provider)).with_state(state.clone());
        let receiver = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let receiver_addr = receiver.local_addr().unwrap();
        let tcp_app = app.clone();
        let server = tokio::spawn(async move {
            axum::serve(receiver, tcp_app).await.unwrap();
        });
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        drop(listener);
        let mut command = Command::new(env!("CARGO_BIN_EXE_pc-gateway"));
        command
            .env_clear()
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .env("PC_GATEWAY_LISTEN", address.to_string())
            .env("PC_GATEWAY_CORE_BASE", format!("http://{receiver_addr}"))
            .env(
                "PC_GATEWAY_UPSTREAM_URL",
                format!("http://{receiver_addr}/provider"),
            )
            .env(
                "PC_GATEWAY_UPSTREAM_URL_RESPONSES",
                format!("http://{receiver_addr}/provider"),
            )
            .env("PC_GATEWAY_MAX_BODY_BYTES", deployment_limit.to_string())
            .env("PC_GATEWAY_INJECTION", if injection { "on" } else { "off" });
        command.env("PC_GATEWAY_MAX_CONCURRENT", "1");
        let uds = combined.then(|| {
            let path = std::env::temp_dir().join(format!("pc2464-{}-{}.sock", std::process::id(), address.port()));
            let listener = tokio::net::UnixListener::bind(&path).unwrap();
            command.env("PC_GATEWAY_CORE_UDS", &path);
            let task = tokio::spawn(async move {
                let mut connections = tokio::task::JoinSet::new();
                loop {
                    tokio::select! {
                        accepted = listener.accept() => {
                            let (stream, _) = accepted.unwrap();
                            let app = app.clone();
                            connections.spawn(async move {
                                let service = hyper::service::service_fn(move |req: hyper::Request<hyper::body::Incoming>| {
                                    use tower::ServiceExt;
                                    app.clone().oneshot(req.map(Body::new))
                                });
                                let _ = hyper::server::conn::http1::Builder::new()
                                    .serve_connection(hyper_util::rt::TokioIo::new(stream), service).await;
                            });
                        }
                        _ = connections.join_next(), if !connections.is_empty() => {}
                    }
                }
            });
            (task, path)
        });
        let steering = steering.then(|| {
            let path = std::env::temp_dir().join(format!(
                "pc2464-{}-{}.txt",
                std::process::id(),
                address.port()
            ));
            std::fs::write(&path, "指令".repeat(100)).unwrap();
            command.env("PC_GATEWAY_STEERING_FILE", &path);
            path
        });
        let mut gateway = Self {
            process: command.spawn().unwrap(),
            server,
            state,
            base: format!("http://{address}"),
            steering,
            uds,
        };
        let client = reqwest::Client::new();
        tokio::time::timeout(Duration::from_secs(10), async {
            loop {
                assert!(
                    gateway.process.try_wait().unwrap().is_none(),
                    "gateway exited during startup"
                );
                if client
                    .get(format!("{}/healthz", gateway.base))
                    .send()
                    .await
                    .is_ok()
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        gateway
    }
    async fn request(&self, route: &str, body: String, chunked: bool) -> (u16, String) {
        if !chunked {
            let response = reqwest::Client::new()
                .post(format!("{}{route}", self.base))
                .header("content-type", "application/json")
                .body(body)
                .send()
                .await
                .unwrap();
            return (response.status().as_u16(), response.text().await.unwrap());
        }
        let mut socket = TcpStream::connect(self.base.trim_start_matches("http://"))
            .await
            .unwrap();
        socket.write_all(format!("POST {route} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n").as_bytes()).await.unwrap();
        for chunk in body.as_bytes().chunks(127) {
            if socket
                .write_all(format!("{:x}\r\n", chunk.len()).as_bytes())
                .await
                .is_err()
            {
                break;
            }
            if socket.write_all(chunk).await.is_err() {
                break;
            }
            if socket.write_all(b"\r\n").await.is_err() {
                break;
            }
        }
        let _ = socket.write_all(b"0\r\n\r\n").await;
        let mut response = vec![];
        let _ = tokio::time::timeout(Duration::from_secs(5), socket.read_to_end(&mut response))
            .await
            .unwrap();
        let response = String::from_utf8(response).unwrap();
        let status = response.split_whitespace().nth(1).unwrap().parse().unwrap();
        (status, response.split_once("\r\n\r\n").unwrap().1.into())
    }
}
const CHAT: &str = "/gateway/v1/chat/completions";
fn chat(text: &str) -> String {
    json!({"model": "test", "messages": [{"role": "user", "content": text}]}).to_string()
}

#[tokio::test]
async fn capability_contract_and_positive_integer_capacity_are_required() {
    let mut invalid = vec![
        json!(null),
        json!({}),
        capabilities(json!(0)),
        capabilities(json!(-1)),
        capabilities(json!(1.5)),
        capabilities(json!("1024")),
    ];
    let mut missing = capabilities(json!(1024));
    missing["context_protection"]["limits"]
        .as_object_mut()
        .unwrap()
        .remove("payload_bytes");
    invalid.push(missing);
    let mut wrong_version = capabilities(json!(1024));
    wrong_version["context_protection"]["check_versions"] = json!(["future.v2"]);
    invalid.push(wrong_version);
    let mut wrong_payload = capabilities(json!(1024));
    wrong_payload["context_protection"]["payload_versions"] = json!([]);
    invalid.push(wrong_payload);
    for caps in invalid {
        let gateway = Gateway::start(caps, 16 * 1024 * 1024, false, false, false, false).await;
        assert_eq!(gateway.request(CHAT, chat("hello"), false).await.0, 503);
        assert_eq!(gateway.state.sends.load(Ordering::SeqCst), 0);
    }
}

#[tokio::test]
async fn incomplete_capability_dto_pauses_even_when_capacity_fields_exist() {
    for (section, field) in [
        ("", "reuse_versions"),
        ("", "reuse_families"),
        ("", "enforcing"),
        ("limits", "binding_bytes"),
    ] {
        let mut caps = capabilities(json!(1024));
        let cp = &mut caps["context_protection"];
        let object = if section.is_empty() {
            cp
        } else {
            &mut cp[section]
        };
        object.as_object_mut().unwrap().remove(field);
        let gateway = Gateway::start(caps, 4096, false, false, false, false).await;
        let (status, body) = gateway.request(CHAT, chat("hello"), false).await;
        assert_eq!(status, 503, "missing {field}: {body}");
        assert!(body.contains("input_capacity_unconfirmed"));
        assert_eq!(gateway.state.sends.load(Ordering::SeqCst), 0);
    }
}

#[tokio::test]
async fn zero_deployment_capacity_reports_configuration_instead_of_core_failure() {
    let gateway = Gateway::start(capabilities(json!(1024)), 0, false, false, false, false).await;
    let (status, body) = gateway.request(CHAT, chat("hello"), false).await;
    assert_eq!(status, 503);
    assert!(body.contains("input_capacity_disabled"), "{body}");
    assert!(!body.contains("input_capacity_unconfirmed"));
    assert_eq!(gateway.state.sends.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn ingress_and_final_wire_bytes_obey_both_limits() {
    for (core_limit, deployment_limit) in [(256, 4096), (4096, 256)] {
        let gateway = Gateway::start(
            capabilities(json!(core_limit)),
            deployment_limit,
            false,
            false,
            false,
            false,
        )
        .await;
        for chunked in [false, true] {
            assert_eq!(
                gateway
                    .request(CHAT, chat(&"界".repeat(100)), chunked)
                    .await
                    .0,
                413
            );
        }
        assert_eq!(gateway.state.sends.load(Ordering::SeqCst), 0);
        assert_eq!(gateway.request(CHAT, chat("ok"), false).await.0, 200);
    }
    // Client bytes fit; renderer-added defaults and control-character escaping make final bytes larger.
    let gateway = Gateway::start(capabilities(json!(256)), 4096, false, false, false, false).await;
    let mut body = "{\"model\":\"test\",\"messages\":[{\"role\":\"user\",\"content\":\"".to_owned();
    body.push_str(&"\\u0001".repeat(31));
    body.push_str("\"}]}");
    assert!(body.len() <= 256);
    let (status, result) = gateway.request(CHAT, body, false).await;
    assert_eq!(status, 403, "{result}");
    assert!(result.contains("request_payload_too_large"));
    assert_eq!(gateway.state.sends.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn prepared_context_and_steering_cannot_bypass_final_capacity() {
    for (injection, steering, route, body) in [
        (true, false, CHAT, chat("hello")),
        (
            false,
            true,
            "/gateway/v1/responses",
            json!({"model": "test", "input": [{"role": "user", "content": "hello"}]}).to_string(),
        ),
    ] {
        let gateway = Gateway::start(
            capabilities(json!(256)),
            4096,
            injection,
            steering,
            false,
            false,
        )
        .await;
        let (status, result) = gateway.request(route, body, false).await;
        assert_eq!(status, 403, "{result}");
        assert!(result.contains("request_payload_too_large"));
        assert_eq!(gateway.state.sends.load(Ordering::SeqCst), 0);
    }
}

#[tokio::test]
async fn increased_configuration_cannot_bypass_compatibility_ceiling() {
    let gateway = Gateway::start(
        capabilities(json!(8 * 1024 * 1024)),
        16 * 1024 * 1024,
        false,
        false,
        false,
        false,
    )
    .await;
    assert_eq!(
        gateway
            .request(CHAT, chat(&"x".repeat(CONTEXT_CHECK_BODY_LIMIT)), false)
            .await
            .0,
        413
    );
    assert_eq!(gateway.state.sends.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn lowered_core_limit_still_requires_a_new_send_verdict() {
    let gateway = Gateway::start(capabilities(json!(4096)), 4096, false, false, false, false).await;
    assert_eq!(gateway.request(CHAT, chat("hello"), false).await.0, 200);
    gateway.state.check_limit.store(32, Ordering::SeqCst);
    assert_eq!(gateway.request(CHAT, chat("hello"), false).await.0, 503);
    assert_eq!(gateway.state.sends.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn response_ceiling_is_independent_of_input_capacity_and_never_releases_a_prefix() {
    for chunked in [false, true] {
        let gateway =
            Gateway::start(capabilities(json!(256)), 4096, false, false, chunked, false).await;
        // Input-only capacity is not an output allowance. Exactly the parser ceiling fits the buffer.
        gateway
            .state
            .response_bytes
            .store(CONTEXT_CHECK_BODY_LIMIT, Ordering::SeqCst);
        assert_eq!(gateway.request(CHAT, chat("hello"), false).await.0, 200);
        gateway
            .state
            .response_bytes
            .store(CONTEXT_CHECK_BODY_LIMIT + 1, Ordering::SeqCst);
        let (status, result) = gateway.request(CHAT, chat("hello"), false).await;
        assert_eq!(status, 409);
        assert!(result.contains("response_buffer_limit_exceeded"));
        assert!(result.len() < 256, "no response prefix released");
        assert_eq!(
            gateway.state.sends.load(Ordering::SeqCst),
            2,
            "no automatic resend"
        );
        gateway.state.response_bytes.store(0, Ordering::SeqCst);
        assert_eq!(gateway.request(CHAT, chat("hello"), false).await.0, 200);
    }
}

#[tokio::test]
async fn exact_final_utf8_and_escaped_json_boundary_is_inclusive() {
    let body = chat("界\n\"\\\u{1}");
    let probe = Gateway::start(capabilities(json!(4096)), 4096, false, false, false, false).await;
    assert_eq!(probe.request(CHAT, body.clone(), false).await.0, 200);
    let wire_bytes = probe.state.checked.lock().unwrap()[0].len();
    assert!(wire_bytes > body.len());
    drop(probe);
    for (limit, status) in [(wire_bytes, 200), (wire_bytes - 1, 403)] {
        let gateway =
            Gateway::start(capabilities(json!(limit)), 4096, false, false, false, false).await;
        assert_eq!(gateway.request(CHAT, body.clone(), true).await.0, status);
        assert_eq!(
            gateway.state.sends.load(Ordering::SeqCst),
            usize::from(status == 200)
        );
    }
}

struct OpenSse(bool);
impl http_body::Body for OpenSse {
    type Data = Bytes;
    type Error = Infallible;
    fn poll_frame(
        mut self: Pin<&mut Self>,
        _: &mut Context<'_>,
    ) -> Poll<Option<Result<http_body::Frame<Bytes>, Infallible>>> {
        if self.0 {
            return Poll::Pending;
        }
        self.0 = true;
        Poll::Ready(Some(Ok(http_body::Frame::data(Bytes::from_static(
            b"data: ready\n\n",
        )))))
    }
}
async fn echo(State(state): State<Arc<Receiver>>, mut body: Body) -> (StatusCode, String) {
    use http_body_util::BodyExt;
    let mut size = 0;
    let mut complete = true;
    while let Some(frame) = body.frame().await {
        match frame {
            Ok(frame) => {
                if let Some(bytes) = frame.data_ref() {
                    size += bytes.len();
                }
            }
            Err(_) => {
                complete = false;
                break;
            }
        }
    }
    state.fallback_seen.lock().unwrap().push((size, complete));
    (
        if complete {
            StatusCode::OK
        } else {
            StatusCode::BAD_REQUEST
        },
        size.to_string(),
    )
}

#[tokio::test]
async fn fallback_keeps_its_capacity_and_active_sse_cannot_starve_models() {
    let gateway = Gateway::start(capabilities(json!(256)), 4096, false, false, false, true).await;
    // A fallback request can exceed the confirmed model capacity without being buffered as a model call.
    for chunked in [false, true] {
        let (status, body) = gateway.request("/echo", "x".repeat(1024), chunked).await;
        assert_eq!(status, 200, "{body}");
    }
    assert_eq!(
        *gateway.state.fallback_seen.lock().unwrap(),
        [(1024, true), (1024, true)]
    );
    let client = reqwest::Client::new();
    let mut sse = client
        .get(format!("{}/sse", gateway.base))
        .send()
        .await
        .unwrap();
    assert_eq!(sse.chunk().await.unwrap().unwrap(), "data: ready\n\n");
    assert_eq!(gateway.request(CHAT, chat("hello"), false).await.0, 200);
    assert_eq!(
        client
            .get(format!("{}/healthz", gateway.base))
            .send()
            .await
            .unwrap()
            .status(),
        StatusCode::OK
    );
    assert_eq!(gateway.request("/echo", "ok".into(), false).await.0, 503);
    drop(sse);
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if gateway.request("/echo", "ok".into(), false).await.0 == 200 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
}

#[tokio::test]
async fn fallback_unknown_length_overflow_is_an_error_and_next_request_works() {
    let gateway = Gateway::start(capabilities(json!(256)), 4096, false, false, false, true).await;
    assert_eq!(
        gateway.request("/echo", "x".repeat(5000), false).await.0,
        413
    );
    assert!(gateway.state.fallback_seen.lock().unwrap().is_empty());
    let (status, body) = gateway.request("/echo", "x".repeat(5000), true).await;
    assert_eq!(status, 502, "{body}");
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            if !gateway.state.fallback_seen.lock().unwrap().is_empty() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let seen = gateway.state.fallback_seen.lock().unwrap().clone();
    assert_eq!(seen.len(), 1);
    assert!(
        !seen[0].1,
        "upstream observed an incomplete request, not successful truncation"
    );
    assert!(seen[0].0 <= 4096);
    assert_eq!(gateway.request("/echo", "ok".into(), false).await.0, 200);
    assert_eq!(
        gateway.state.fallback_seen.lock().unwrap().last(),
        Some(&(2, true))
    );
}
