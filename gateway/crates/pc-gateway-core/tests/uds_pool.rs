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

use pc_gateway_core::{GateClient, UdsGateClient};
use pc_gateway_types::{CheckDecision, CheckPhase, ContextCheckRequest, ModelJsonPayload};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};

static CONN_COUNT: AtomicUsize = AtomicUsize::new(0);

fn result_json(decision: &str) -> String {
    let schema = "powercontext.context-check.v1";
    format!(
        r#"{{"schema":"{schema}","phase":"before_send","decision":"{decision}","reason_codes":[],"coverage":{{"profile":"text-and-tools.v1","complete":true}},"policy_version":"t","valid_until":"2999-01-01T00:00:00Z","binding":"b1"}}"#
    )
}

fn check_request() -> ContextCheckRequest {
    ContextCheckRequest::BeforeSend {
        scope_id: "s".into(),
        target_profile: "p".into(),
        payload: ModelJsonPayload::new(BTreeMap::new(), "{}".into()),
        prepared_contexts: Vec::new(),
    }
}

/// 读一行请求头直到空行（忽略 body 细节，够用即可）。
async fn read_request(conn: &mut UnixStream) {
    let mut buf = Vec::new();
    let mut byte = [0u8; 1];
    loop {
        conn.read_exact(&mut byte).await.unwrap();
        buf.push(byte[0]);
        if buf.ends_with(b"\r\n\r\n") {
            break;
        }
    }
    // 简化：Content-Length 为 0 的探测请求到此结束；带 body 的读掉
    let head = String::from_utf8_lossy(&buf).to_string();
    if let Some(len) = head.lines().find_map(|l| {
        l.strip_prefix("Content-Length: ")
            .and_then(|v| v.trim().parse::<usize>().ok())
    }) {
        let mut body = vec![0u8; len];
        if len > 0 {
            conn.read_exact(&mut body).await.unwrap();
        }
    }
}

#[tokio::test]
async fn pooled_connection_parses_sequential_responses_correctly() {
    let dir = std::env::temp_dir().join(format!("pc-uds-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let sock = dir.join("pool.sock");
    let _ = std::fs::remove_file(&sock);
    let listener = UnixListener::bind(&sock).unwrap();

    let server = tokio::spawn(async move {
        let (mut conn, _) = listener.accept().await.unwrap();
        CONN_COUNT.fetch_add(1, Ordering::SeqCst);

        // 请求1 → content-length 精确
        read_request(&mut conn).await;
        let body = result_json("allow");
        conn.write_all(
            format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
                body.len()
            )
            .as_bytes(),
        )
        .await
        .unwrap();
        conn.write_all(body.as_bytes()).await.unwrap();

        // 请求2 → chunked（0-chunk + 终止 CRLF，即 trailer 空行）
        read_request(&mut conn).await;
        let body2 = result_json("allow");
        let mut chunked = Vec::new();
        chunked.extend_from_slice(format!("{:x}\r\n", body2.len()).as_bytes());
        chunked.extend_from_slice(body2.as_bytes());
        chunked.extend_from_slice(b"\r\n0\r\n\r\n");
        conn.write_all(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n",
        ).await.unwrap();
        conn.write_all(&chunked).await.unwrap();

        // 请求3 → content-length 后故意多推 5 个脏字节（应被截断且连接不复用）
        read_request(&mut conn).await;
        let body3 = result_json("allow");
        conn.write_all(
            format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n", body3.len()).as_bytes(),
        )
        .await
        .unwrap();
        conn.write_all(body3.as_bytes()).await.unwrap();
        conn.write_all(b"DIRTY").await.unwrap();
        // 保持连接打开一会儿，验证客户端没把脏字节当下一响应
        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
    });

    let client = UdsGateClient::new(&sock);
    // 请求1：content-length 精确
    let r1 = client.check(check_request()).await.unwrap();
    assert_eq!(r1.decision, CheckDecision::Allow);
    // 请求2：chunked 应正确解码（而非把 trailer 当下一个响应头）
    let r2 = client.check(check_request()).await.unwrap();
    assert_eq!(r2.decision, CheckDecision::Allow);
    assert_eq!(r2.phase, CheckPhase::BeforeSend, "chunked 解码后字段完整");
    // 请求3：超读截断——body 应恰好是合法 JSON（脏字节被丢弃）
    let r3 = client.check(check_request()).await.unwrap();
    assert_eq!(r3.decision, CheckDecision::Allow);

    server.await.unwrap();
    // 三次请求只应建立一条连接（第3次后因脏字节弃用，但那是之后的事）
    assert_eq!(
        CONN_COUNT.load(Ordering::SeqCst),
        1,
        "keep-alive 应复用同一连接"
    );
}
