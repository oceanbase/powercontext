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

//! 回归测试（PR #2307 review P1-1）：
//! 对端读完整 POST body 后关闭连接 —— reqwest 报 is_request() 类错误。
//! 断言：body 只被上游收到一次（绝不重复计费发送），
//! 且结果归为 Indeterminate 而非可重试的 NotSent。

use pc_gateway_transport::{AdmittedSend, ReqwestTransport, SendIndeterminateReason, SendOutcome};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use tokio::io::AsyncReadExt;
use tokio::net::TcpListener;

#[tokio::test]
async fn upstream_closing_after_full_body_is_indeterminate_and_not_resent() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let bodies = Arc::new(AtomicUsize::new(0));
    let connections = Arc::new(AtomicUsize::new(0));

    let b = bodies.clone();
    let c = connections.clone();
    tokio::spawn(async move {
        // 接受至多 2 条连接：若网关错误地重试，第二条也会收到 body。
        for _ in 0..2 {
            let (mut sock, _) = match listener.accept().await {
                Ok(x) => x,
                Err(_) => return,
            };
            c.fetch_add(1, Ordering::SeqCst);
            let mut buf = Vec::new();
            let mut chunk = [0u8; 4096];
            // 读到完整请求（头 + Content-Length 声明的 body）
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
            // 完整收到一条带 body 的 POST → 计数
            if buf.starts_with(b"POST") {
                b.fetch_add(1, Ordering::SeqCst);
            }
            // 读完即关闭，不回任何响应（review 描述的复现场景）
            drop(sock);
        }
    });

    let transport = ReqwestTransport::default();
    let send = AdmittedSend {
        binding: "b".into(),
        payload: bytes::Bytes::from_static(br#"{"model":"m","messages":[]}"#),
        target_url: format!("http://127.0.0.1:{port}/v1/chat/completions"),
        headers: vec![],
        stream: false,
    };

    // 复现 review 场景：BoundedRetry(max_attempts=2)。
    // 旧行为：is_request() 被归为 NotSent → 重试 → 上游收到两次完整 body
    // 且最终谎报 AllNotSent{attempts:2}。
    // 新行为：归为 Indeterminate → 立即上抛，不进行第二次发送。
    let retry =
        pc_gateway_transport::BoundedRetry::new(transport, std::num::NonZeroU32::new(2).unwrap());
    let outcome = retry
        .send_with_retry(&send, &pc_gateway_evidence::NoopEvidenceSink)
        .await;
    match outcome {
        pc_gateway_transport::RetryOutcome::Done(SendOutcome::Indeterminate(
            SendIndeterminateReason::LostAfterHandoff,
        )) => {}
        other => panic!("expected Done(Indeterminate), got {other:?}"),
    }

    tokio::time::sleep(std::time::Duration::from_millis(200)).await;
    assert_eq!(
        bodies.load(Ordering::SeqCst),
        1,
        "上游必须只收到一次 body（修复前是 2 次，重复计费）"
    );
}
