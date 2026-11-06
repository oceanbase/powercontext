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

//! 回归测试（PR #2307 review P1-2）：
//! 获准上游返回 307 时，reqwest 默认会把已准入的 POST body 原样重发到
//! Location 指向的第二个地址（未经 Core 准入）。禁用自动跟随后：
//! 第二地址必须收到零字节；307 响应原样透传（Delivered status=307）。

use pc_gateway_transport::{AdmittedSend, ReqwestTransport, SendOutcome, Transport};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::Arc;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

/// 读一条完整 HTTP 请求（头 + content-length body），返回原始字节。
async fn read_request(sock: &mut TcpStream) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        let n = sock.read(&mut chunk).await.unwrap_or(0);
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
        if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            let head = String::from_utf8_lossy(&buf[..pos]).to_ascii_lowercase();
            let len: usize = head
                .lines()
                .find_map(|l| l.strip_prefix("content-length: "))
                .and_then(|v| v.trim().parse().ok())
                .unwrap_or(0);
            if buf.len() >= pos + 4 + len {
                break;
            }
        }
    }
    buf
}

#[tokio::test]
async fn redirect_target_receives_nothing_and_307_surfaces() {
    // 第二地址（重定向目标）：任何到达的字节都记为违规
    let evil = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let evil_port = evil.local_addr().unwrap().port();
    let evil_received = Arc::new(AtomicUsize::new(0));
    let er = evil_received.clone();
    tokio::spawn(async move {
        if let Ok((mut sock, _)) = evil.accept().await {
            let data = read_request(&mut sock).await;
            er.fetch_add(data.len(), Ordering::SeqCst);
            let resp = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok";
            let _ = sock.write_all(resp).await;
        }
    });

    // 第一地址（获准上游）：读完整请求后回 307 指向第二地址
    let approved = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let approved_port = approved.local_addr().unwrap().port();
    let served_307 = Arc::new(AtomicBool::new(false));
    let s307 = served_307.clone();
    let evil_addr = format!("127.0.0.1:{evil_port}");
    tokio::spawn(async move {
        let (mut sock, _) = approved.accept().await.unwrap();
        let _ = read_request(&mut sock).await;
        let resp = format!(
            "HTTP/1.1 307 Temporary Redirect\r\nLocation: http://{evil_addr}/steer\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
        );
        sock.write_all(resp.as_bytes()).await.unwrap();
        s307.store(true, Ordering::SeqCst);
    });

    let transport = ReqwestTransport::default();
    let send = AdmittedSend {
        binding: "b".into(),
        payload: bytes::Bytes::from_static(br#"{"model":"m","messages":[]}"#),
        target_url: format!("http://127.0.0.1:{approved_port}/v1/chat/completions"),
        headers: vec![],
        stream: false,
    };
    let outcome = transport.send(send).await;

    // 307 原样上抛（不跟随、不报错、不重发）
    match outcome {
        SendOutcome::Delivered(raw) => assert_eq!(raw.status, 307, "307 必须原样透传"),
        other => panic!("expected Delivered(307), got {other:?}"),
    }
    assert!(served_307.load(Ordering::SeqCst), "获准地址已返回 307");

    tokio::time::sleep(std::time::Duration::from_millis(300)).await;
    // 关键断言：重定向目标收到的字节数为零（修复前会收到完整 POST body）
    assert_eq!(
        evil_received.load(Ordering::SeqCst),
        0,
        "重定向目标必须收到零字节（获准目的地=实际目的地）"
    );
}
