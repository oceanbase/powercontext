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

//! 回归测试（PR #2307 review P2）：
//! 非流式路径的字节上限。with_buffer_limit(16) 下收到 1KB 响应必须
//! 判为 Indeterminate(BufferLimitExceeded)，不得 Delivered。

use pc_gateway_transport::{
    AdmittedSend, ReqwestTransport, SendIndeterminateReason, SendOutcome, Transport,
};
use std::time::Duration;
use tokio::io::AsyncWriteExt;
use tokio::net::TcpListener;

#[tokio::test]
async fn nonstreaming_response_over_buffer_limit_is_rejected() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        let (mut sock, _) = listener.accept().await.unwrap();
        // 读掉请求（忽略内容，client 会发完整 POST）
        let mut buf = vec![0u8; 8192];
        let _ = tokio::time::timeout(Duration::from_secs(5), async {
            use tokio::io::AsyncReadExt as _;
            let _ = sock.read(&mut buf).await;
        })
        .await;
        // 回一个超过 16 字节上限的 JSON 响应
        let body = format!("{{\"padding\":\"{}\"}}", "x".repeat(1024));
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        let _ = sock.write_all(resp.as_bytes()).await;
    });

    let transport = ReqwestTransport::new(Duration::from_secs(10)).with_buffer_limit(16);
    let send = AdmittedSend {
        binding: "b".into(),
        payload: bytes::Bytes::from_static(b"{}"),
        target_url: format!("http://127.0.0.1:{port}/v1/chat/completions"),
        headers: vec![],
        stream: false, // 关键：非流式路径
    };
    let outcome = transport.send(send).await;
    match outcome {
        SendOutcome::Indeterminate(SendIndeterminateReason::BufferLimitExceeded) => {}
        other => panic!("expected Indeterminate(BufferLimitExceeded), got {other:?}"),
    }
}

#[tokio::test]
async fn nonstreaming_response_within_limit_still_delivers() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        let (mut sock, _) = listener.accept().await.unwrap();
        let mut buf = vec![0u8; 8192];
        let _ = tokio::time::timeout(Duration::from_secs(5), async {
            use tokio::io::AsyncReadExt as _;
            let _ = sock.read(&mut buf).await;
        })
        .await;
        let body = r#"{"ok":true}"#;
        let resp = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        let _ = sock.write_all(resp.as_bytes()).await;
    });

    let transport = ReqwestTransport::new(Duration::from_secs(10)).with_buffer_limit(1024);
    let send = AdmittedSend {
        binding: "b".into(),
        payload: bytes::Bytes::from_static(b"{}"),
        target_url: format!("http://127.0.0.1:{port}/v1/chat/completions"),
        headers: vec![],
        stream: false,
    };
    let outcome = transport.send(send).await;
    match outcome {
        SendOutcome::Delivered(raw) => {
            assert_eq!(raw.status, 200);
            let v: serde_json::Value = serde_json::from_slice(&raw.body).unwrap();
            assert_eq!(v["ok"], true);
        }
        other => panic!("expected Delivered, got {other:?}"),
    }
}
