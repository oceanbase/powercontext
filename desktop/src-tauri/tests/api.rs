/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

use powercontext_desktop::{
    error::SafeError,
    transport::{Endpoint, ServerApi, wire::ReadinessStatus},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};

async fn fixture(status: u16, body: &'static str) -> (ServerApi, tokio::task::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Endpoint::parse(&format!(
        "http://{}/proxy/%E4%B8%AD%E6%96%87",
        listener.local_addr().unwrap()
    ))
    .unwrap();
    let task = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut bytes = vec![];
        loop {
            let mut buf = [0; 1024];
            let n = socket.read(&mut buf).await.unwrap();
            if n == 0 {
                break;
            }
            bytes.extend_from_slice(&buf[..n]);
            if let Some(end) = bytes.windows(4).position(|v| v == b"\r\n\r\n") {
                let headers = String::from_utf8_lossy(&bytes[..end]);
                let size: usize = headers
                    .lines()
                    .find_map(|l| {
                        l.to_lowercase()
                            .strip_prefix("content-length: ")
                            .map(str::to_owned)
                    })
                    .map(|s| s.parse().unwrap())
                    .unwrap_or(0);
                if bytes.len() >= end + 4 + size {
                    break;
                }
            }
        }
        socket.write_all(format!("HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nX-PowerContext-Request-ID: safe-request-1\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).await.unwrap();
        String::from_utf8(bytes).unwrap()
    });
    (ServerApi::new(endpoint, None, None).unwrap(), task)
}
#[tokio::test]
async fn scope_paging_is_bounded_and_preserves_opaque_cursor_and_prefix() {
    let (api, request) = fixture(200, r#"{"items":[],"next_cursor":null}"#).await;
    api.scopes("中文 & title", Some("opaque+/=?"))
        .await
        .unwrap();
    let request = request.await.unwrap();
    assert!(request.starts_with("GET /proxy/%E4%B8%AD%E6%96%87/v1/scopes?limit=50&"));
    assert!(request.contains("query_field=title"));
    assert!(request.contains("cursor=opaque%2B%2F%3D%3F"));
}
#[tokio::test]
async fn exact_scope_uses_one_encoded_path_segment() {
    let (api, request) = fixture(404, "private body").await;
    assert_eq!(
        api.scope("scope/中文?x").await.err().unwrap().code,
        SafeError::NotFound
    );
    assert!(
        request
            .await
            .unwrap()
            .starts_with("GET /proxy/%E4%B8%AD%E6%96%87/v1/scopes/scope%2F%E4%B8%AD%E6%96%87%3Fx ")
    );
}
#[tokio::test]
async fn readiness_failure_is_a_fact_and_write_failure_keeps_dispatch_uncertainty() {
    let (api, request) = fixture(
        503,
        r#"{"status":"not_ready","checks":{"database":"not_ready"}}"#,
    )
    .await;
    assert_eq!(
        api.readiness().await.unwrap().status,
        ReadinessStatus::NotReady
    );
    request.await.unwrap();
    let (api, request) = fixture(500, "private body synthetic-secret").await;
    let error = api
        .remember("selected-scope", "synthetic note")
        .await
        .err()
        .unwrap();
    assert!(error.dispatched);
    assert_eq!(error.code, SafeError::Server);
    assert_eq!(error.request_id.as_deref(), Some("safe-request-1"));
    assert!(
        !serde_json::to_string(&error)
            .unwrap()
            .contains("private body")
    );
    let request = request.await.unwrap();
    let body: serde_json::Value =
        serde_json::from_str(request.split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!(
        body,
        serde_json::json!({"scope_id":"selected-scope","kind":"note","text":"synthetic note"})
    );
}
#[tokio::test]
async fn errors_are_distinct_without_anonymous_fallback() {
    for (status, code) in [
        (401, SafeError::Unauthorized),
        (403, SafeError::Forbidden),
        (410, SafeError::CursorExpired),
    ] {
        let (api, task) = fixture(status, "sensitive failure details").await;
        assert_eq!(api.scopes("", None).await.err().unwrap().code, code);
        task.await.unwrap();
    }
    let (api, task) = fixture(
        503,
        r#"{"error":{"code":"authentication_unavailable","message":"sensitive failure details"}}"#,
    )
    .await;
    assert_eq!(
        api.principal().await.err().unwrap().code,
        SafeError::AuthenticationUnavailable
    );
    task.await.unwrap();
}
#[test]
fn encoded_base_paths_reject_escape_aliases_and_support_unicode() {
    for endpoint in [
        "http://127.1/",
        "http://2130706433/",
        "http://localhost/a/%2e%2e/b",
        "http://localhost/%252e",
        "http://localhost/a%2fb",
        "http://localhost/%xx",
        "http://localhost/%ff",
    ] {
        assert!(Endpoint::parse(endpoint).is_err(), "{endpoint}");
    }
    assert_eq!(
        Endpoint::parse("http://localhost/中文/").unwrap().as_str(),
        "http://localhost/%E4%B8%AD%E6%96%87/"
    );
    assert_eq!(
        Endpoint::parse("http://localhost/%E4%B8%AD%E6%96%87/")
            .unwrap()
            .as_str(),
        "http://localhost/%E4%B8%AD%E6%96%87/"
    );
}

#[tokio::test]
async fn invalid_successful_write_response_keeps_dispatch_uncertainty() {
    let (api, request) = fixture(200, r#"{"memory":{"family":"memory","artifact_id":"m","revision":9007199254740992},"entry":null}"#).await;
    let error = api
        .remember("scope-a", "synthetic note")
        .await
        .err()
        .unwrap();
    assert_eq!(error.code, SafeError::InvalidResponse);
    assert!(error.dispatched);
    request.await.unwrap();
}
