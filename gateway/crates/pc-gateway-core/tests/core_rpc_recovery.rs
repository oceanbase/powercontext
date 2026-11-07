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

//! UDS 连接池正确性回归：keep-alive 连接上的连续响应必须逐次正确解析。
//! 覆盖三个场景：content-length 精确、chunked（含 trailer）、content-length 超读截断。

use pc_gateway_core::{GateClient, GateError, HttpGateClient, UdsGateClient};
use pc_gateway_types::{
    CaptureContentSourceRequest, ContextCheckRequest, ModelJsonPayload, PrepareContextRequest,
};
use serde_json::{json, Value};
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

trait Socket: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Socket for T {}

async fn request(stream: &mut (impl AsyncRead + Unpin + ?Sized)) -> Vec<u8> {
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
        .map(|s| s.parse().unwrap())
        .unwrap_or(0);
    let mut body = vec![0; length];
    stream.read_exact(&mut body).await.unwrap();
    head.extend(body);
    head
}

async fn invoke(client: &impl GateClient, operation: &str) -> Result<Value, GateError> {
    match operation {
        "capabilities" => client.capabilities().await,
        "check" => client
            .check(ContextCheckRequest::BeforeSend {
                scope_id: "scope".into(),
                target_profile: "model".into(),
                payload: ModelJsonPayload::new(Default::default(), "{}".into()),
                prepared_contexts: vec![],
            })
            .await
            .map(|v| serde_json::to_value(v).unwrap()),
        "capture" => {
            client
                .capture(CaptureContentSourceRequest {
                    scope_id: "scope".into(),
                    source_id: "stable-source".into(),
                    content: "准确正文".into(),
                    metadata: Some(json!({"nested": {"key": ["value", 1]}, "other": true})),
                })
                .await
        }
        "prepare" => client
            .prepare(PrepareContextRequest {
                scope_id: "scope".into(),
                query: "hello".into(),
                max_bytes: 1024,
                include_reuse: false,
                include_code: false,
                assembly: None,
            })
            .await
            .map(|v| serde_json::to_value(v).unwrap()),
        _ => unreachable!(),
    }
}

async fn scenario(uds: bool, operation: &str, failure: &[u8], retry: bool) {
    static NEXT_SOCKET: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let socket_id = NEXT_SOCKET.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let socket_path = std::env::temp_dir().join(format!(
        "pc-rpc-{}-{operation}-{socket_id}.sock",
        std::process::id()
    ));
    let _ = std::fs::remove_file(&socket_path);
    let unix = if uds {
        Some(tokio::net::UnixListener::bind(&socket_path).unwrap())
    } else {
        None
    };
    let tcp = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", tcp.local_addr().unwrap());
    let failure = failure.to_vec();
    let operation_owned = operation.to_owned();
    let server = tokio::spawn(async move {
        let accept = || async {
            if let Some(unix) = &unix {
                Box::new(unix.accept().await.unwrap().0) as Box<dyn Socket>
            } else {
                Box::new(tcp.accept().await.unwrap().0) as Box<dyn Socket>
            }
        };
        let mut first = accept().await;
        let original = request(&mut first).await;
        first.write_all(&failure).await.unwrap();
        first.shutdown().await.unwrap();
        drop(first);
        if !retry {
            assert!(
                tokio::time::timeout(Duration::from_millis(100), accept())
                    .await
                    .is_err(),
                "{operation_owned} must not replay"
            );
            return;
        }
        let mut second = accept().await;
        let repeated = request(&mut second).await;
        assert_eq!(
            original, repeated,
            "recovery must replay identical bytes including full metadata"
        );
        let body = if operation_owned == "check" {
            json!({"schema":"powercontext.context-check.v1", "phase":"before_send", "decision":"allow",
                "reason_codes":[], "coverage":{"profile":"test", "complete":true},
                "policy_version":"test", "binding":"received-binding", "valid_until":"2999-01-01T00:00:00Z"})
        } else { json!({"record":"original", "journal_position":7}) }.to_string();
        second
            .write_all(
                format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .as_bytes(),
            )
            .await
            .unwrap();
        second.shutdown().await.unwrap();
        assert!(
            tokio::time::timeout(Duration::from_millis(100), accept())
                .await
                .is_err(),
            "at most one retry"
        );
    });
    let result = if uds {
        invoke(&UdsGateClient::with_token(&socket_path, None), operation).await
    } else {
        invoke(&HttpGateClient::with_token(base, None), operation).await
    };
    if retry {
        let result = result.unwrap();
        if operation == "check" {
            assert_eq!(result["binding"], "received-binding");
        } else {
            assert_eq!(result["journal_position"], 7);
        }
    } else {
        assert!(result.is_err());
    }
    tokio::time::timeout(Duration::from_secs(3), server)
        .await
        .unwrap()
        .unwrap();
    if uds {
        std::fs::remove_file(socket_path).unwrap();
    }
}

#[tokio::test]
async fn readonly_and_exact_capture_recover_from_response_loss_over_http_and_uds() {
    for uds in [false, true] {
        for operation in ["capabilities", "check", "capture"] {
            scenario(uds, operation, b"", true).await;
            scenario(
                uds,
                operation,
                b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{",
                true,
            )
            .await;
        }
    }
}

#[tokio::test]
async fn prepare_handoff_rejections_and_malformed_json_are_never_replayed() {
    for uds in [false, true] {
        scenario(uds, "prepare", b"", false).await;
        scenario(uds, "capabilities", b"NOT HTTP\r\n\r\n", false).await;
        for operation in ["capabilities", "check", "capture", "prepare"] {
            for status in [401, 409, 422] {
                scenario(
                    uds,
                    operation,
                    format!("HTTP/1.1 {status} Rejected\r\nContent-Length: 100\r\n\r\n").as_bytes(),
                    false,
                )
                .await;
            }
            scenario(
                uds,
                operation,
                b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\n\r\nx",
                false,
            )
            .await;
        }
    }
}

#[tokio::test]
async fn retry_waits_consume_original_deadline_and_cancellation_then_release_connections() {
    use pc_gateway_core::{LifecycleStopReason, RequestControl};
    for uds in [false, true] {
        for cancel in [false, true] {
            let path = std::env::temp_dir().join(format!(
                "pc-rpc-stop-{}-{uds}-{cancel}.sock",
                std::process::id()
            ));
            let _ = std::fs::remove_file(&path);
            let unix = if uds {
                Some(tokio::net::UnixListener::bind(&path).unwrap())
            } else {
                None
            };
            let tcp = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let base = format!("http://{}", tcp.local_addr().unwrap());
            let control = RequestControl::new(
                tokio::time::Instant::now() + Duration::from_millis(100),
                None,
            );
            let cancellation = control.cancellation();
            let server = tokio::spawn(async move {
                let accept = || async {
                    if let Some(unix) = &unix {
                        Box::new(unix.accept().await.unwrap().0) as Box<dyn Socket>
                    } else {
                        Box::new(tcp.accept().await.unwrap().0) as Box<dyn Socket>
                    }
                };
                let mut first = accept().await;
                request(&mut first).await;
                drop(first);
                let mut retry = accept().await;
                request(&mut retry).await;
                retry
                    .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 999999\r\n\r\n")
                    .await
                    .unwrap();
                if cancel {
                    cancellation.cancel();
                }
                while retry.write_all(b"x").await.is_ok() {
                    tokio::time::sleep(Duration::from_millis(5)).await;
                }
                let mut recovered = accept().await;
                request(&mut recovered).await;
                recovered
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}",
                    )
                    .await
                    .unwrap();
            });
            let began = tokio::time::Instant::now();
            let lifecycle = control.lifecycle();
            if uds {
                let client = UdsGateClient::with_token(&path, None);
                assert_eq!(
                    lifecycle.run(client.capabilities()).await.unwrap_err(),
                    if cancel {
                        LifecycleStopReason::RequestCancelled
                    } else {
                        LifecycleStopReason::DeadlineExceeded
                    }
                );
                assert_eq!(
                    tokio::time::timeout(Duration::from_secs(2), client.capabilities())
                        .await
                        .unwrap()
                        .unwrap(),
                    json!({})
                );
            } else {
                let client = HttpGateClient::with_token(base, None);
                assert_eq!(
                    lifecycle.run(client.capabilities()).await.unwrap_err(),
                    if cancel {
                        LifecycleStopReason::RequestCancelled
                    } else {
                        LifecycleStopReason::DeadlineExceeded
                    }
                );
                assert_eq!(
                    tokio::time::timeout(Duration::from_secs(2), client.capabilities())
                        .await
                        .unwrap()
                        .unwrap(),
                    json!({})
                );
            }
            assert!(began.elapsed() < Duration::from_secs(2));
            server.await.unwrap();
            if uds {
                std::fs::remove_file(path).unwrap();
            }
        }
    }
}
