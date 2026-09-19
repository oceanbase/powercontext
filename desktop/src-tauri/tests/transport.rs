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
    credentials::Secret,
    error::SafeError,
    transport::{Endpoint, MAX_RESPONSE_BYTES, Transport},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};

async fn fixture(response: String) -> (Endpoint, tokio::task::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Endpoint::parse(&format!(
        "http://{}/proxy/powercontext",
        listener.local_addr().unwrap()
    ))
    .unwrap();
    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = vec![0; 8192];
        let count = stream.read(&mut request).await.unwrap();
        stream.write_all(response.as_bytes()).await.unwrap();
        String::from_utf8_lossy(&request[..count]).into_owned()
    });
    (endpoint, task)
}
fn response(status: &str, body: &str) -> String {
    format!(
        "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
}

#[tokio::test]
async fn read_budget_rejects_excess_work_and_recovers_after_cancellation() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Endpoint::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let (accepted, mut observed) = tokio::sync::mpsc::channel(4);
    let server = tokio::spawn(async move {
        let mut sockets = Vec::new();
        loop {
            let (socket, _) = listener.accept().await.unwrap();
            sockets.push(socket);
            if accepted.send(()).await.is_err() {
                break;
            }
        }
    });
    let transport = Transport::new(None).unwrap();
    let mut requests = Vec::new();
    for _ in 0..4 {
        let client = transport.clone();
        let endpoint = endpoint.clone();
        requests.push(tokio::spawn(async move {
            client.liveness(&endpoint, None).await
        }));
        tokio::time::timeout(std::time::Duration::from_secs(5), observed.recv())
            .await
            .unwrap()
            .unwrap();
    }
    assert_eq!(
        transport.liveness(&endpoint, None).await,
        Err(SafeError::Busy)
    );
    for request in requests {
        request.abort();
        let _ = request.await;
    }
    server.abort();
    let (endpoint, server) = fixture(response("200 OK", r#"{"status":"ok"}"#)).await;
    transport.liveness(&endpoint, None).await.unwrap();
    server.await.unwrap();
}

#[tokio::test]
async fn ignores_proxy_environment() {
    if let Ok(url) = std::env::var("S1_CHILD_ENDPOINT") {
        Transport::new(None)
            .unwrap()
            .liveness(&Endpoint::parse(&url).unwrap(), None)
            .await
            .unwrap();
        return;
    }
    let (endpoint, server) = fixture(response("200 OK", r#"{"status":"ok"}"#)).await;
    let url = endpoint.as_str().to_owned();
    let child = tokio::task::spawn_blocking(move || {
        std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "ignores_proxy_environment"])
            .env("S1_CHILD_ENDPOINT", url)
            .env("HTTP_PROXY", "http://127.0.0.1:1")
            .env("HTTPS_PROXY", "http://127.0.0.1:1")
            .env("ALL_PROXY", "http://127.0.0.1:1")
            .env("NO_PROXY", "")
            .status()
            .unwrap()
    });
    assert!(child.await.unwrap().success());
    server.await.unwrap();
}

#[tokio::test]
async fn stalled_response_times_out() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Endpoint::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap();
    let server = tokio::spawn(async move {
        let (_stream, _) = listener.accept().await.unwrap();
        std::future::pending::<()>().await;
    });
    assert_eq!(
        Transport::new(None)
            .unwrap()
            .liveness(&endpoint, None)
            .await,
        Err(SafeError::Timeout)
    );
    server.abort();
}

#[tokio::test]
async fn rejects_chunked_overflow_without_silent_truncation() {
    let body = "x".repeat(MAX_RESPONSE_BYTES + 1);
    let (endpoint, server) = fixture(format!(
        "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n{:x}\r\n{}\r\n0\r\n\r\n",
        body.len(),
        body
    ))
    .await;
    assert_eq!(
        Transport::new(None)
            .unwrap()
            .liveness(&endpoint, None)
            .await,
        Err(SafeError::ResponseTooLarge)
    );
    let _ = server.await;
}
#[test]
fn shared_loopback_policy_and_unsafe_addresses() {
    let vectors: serde_json::Value = serde_json::from_str(include_str!(
        "../../../tests/fixtures/transport_loopback_vectors.json"
    ))
    .unwrap();
    for host in vectors["loopback"].as_array().unwrap() {
        assert!(Endpoint::parse(&format!("http://{}:8000", host.as_str().unwrap())).is_ok());
    }
    for host in vectors["non_loopback"].as_array().unwrap() {
        assert!(matches!(
            Endpoint::parse(&format!("http://{}:8000", host.as_str().unwrap())),
            Err(SafeError::InsecureTransport)
        ));
    }
    for bad in [
        "https://user:secret@example.com",
        "https://@example.com",
        "https://example.com?x=1",
        "https://example.com/#hash",
        "https://example.com/a/../b",
        "https://example.com/%2e%2e/b",
        "https://example.com/a\\b",
        "file:///tmp/x",
        " https://example.com",
        "https://example.com/\n",
    ] {
        assert!(Endpoint::parse(bad).is_err(), "{bad}");
    }
}
#[tokio::test]
async fn preserves_reverse_proxy_path_and_keeps_auth_in_headers() {
    let (endpoint, server) = fixture(response("200 OK", r#"{"status":"ok"}"#)).await;
    Transport::new(None)
        .unwrap()
        .liveness(
            &endpoint,
            Some(&Secret::new("synthetic-test-only".into()).unwrap()),
        )
        .await
        .unwrap();
    let request = server.await.unwrap();
    assert!(request.starts_with("GET /proxy/powercontext/health/live HTTP/1.1"));
    assert!(
        request
            .to_lowercase()
            .contains("authorization: bearer synthetic-test-only")
    );
    assert!(!request.lines().next().unwrap().contains("synthetic"));
}
#[tokio::test]
async fn redirects_are_not_followed_even_with_a_credential() {
    let destination = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let redirect = format!(
        "HTTP/1.1 302 Found\r\nLocation: http://{}/capture\r\nContent-Length: 0\r\n\r\n",
        destination.local_addr().unwrap()
    );
    let (endpoint, server) = fixture(redirect).await;
    assert_eq!(
        Transport::new(None)
            .unwrap()
            .liveness(&endpoint, Some(&Secret::new("synthetic".into()).unwrap()))
            .await,
        Err(SafeError::Redirect)
    );
    server.await.unwrap();
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(100), destination.accept())
            .await
            .is_err()
    );
}
#[tokio::test]
async fn projects_errors_without_server_body_or_secrets() {
    for (status, error) in [
        ("401 Unauthorized", SafeError::Unauthorized),
        ("403 Forbidden", SafeError::Forbidden),
        ("503 Unavailable", SafeError::Server),
    ] {
        let (endpoint, server) = fixture(response(status, "private-body-and-token")).await;
        let result = Transport::new(None)
            .unwrap()
            .liveness(&endpoint, None)
            .await
            .unwrap_err();
        assert_eq!(result, error);
        assert!(!serde_json::to_string(&result).unwrap().contains("private"));
        server.await.unwrap();
    }
}
#[tokio::test]
async fn rejects_oversized_and_invalid_responses() {
    for body in [
        "x".repeat(MAX_RESPONSE_BYTES + 1),
        r#"{"status":"degraded"}"#.into(),
        "<script>evil()</script>".into(),
    ] {
        let (endpoint, server) = fixture(response("200 OK", &body)).await;
        let result = Transport::new(None)
            .unwrap()
            .liveness(&endpoint, None)
            .await;
        assert!(matches!(
            result,
            Err(SafeError::ResponseTooLarge | SafeError::InvalidResponse)
        ));
        // Client may close before the fixture finishes writing an oversized body.
        let _ = server.await;
    }
}
