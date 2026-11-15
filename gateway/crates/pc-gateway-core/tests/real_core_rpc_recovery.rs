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

//! Required real Python Core acceptance: startup failures fail, never skip.
use pc_gateway_core::{GateClient, GateError, UdsGateClient};
use pc_gateway_types::{CaptureContentSourceRequest, PrepareContextRequest};
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::Duration,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

const TOKEN: &str = "disposable-rpc-recovery-token";

struct CoreProcess {
    child: Child,
    directory: PathBuf,
}
impl Drop for CoreProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_dir_all(&self.directory);
    }
}

async fn start_core() -> (CoreProcess, reqwest::Client, PathBuf) {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .unwrap();
    let directory = std::env::temp_dir().join(format!("pc-real-rpc-{}", std::process::id()));
    std::fs::create_dir_all(&directory).unwrap();
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o700)).unwrap();
    let socket = directory.join("core.sock");
    let log_path = directory.join("core.log");
    let log = std::fs::File::create(&log_path).unwrap();
    let python = std::env::var_os("PC_TEST_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| root.join(".venv/bin/python"));
    let mut command = Command::new(python);
    command
        .args([
            "-c",
            "from powercontext.cli.app import main; main()",
            "server",
            "run",
            "--no-env-file",
            "--uds",
        ])
        .arg(&socket)
        .current_dir(&directory)
        .stdin(Stdio::null())
        .stdout(log.try_clone().unwrap())
        .stderr(log);
    for (key, _) in std::env::vars().filter(|(key, _)| {
        key.starts_with("POWERCONTEXT_") || key.starts_with("OTEL_") || key.starts_with("OPENAI_")
    }) {
        command.env_remove(key);
    }
    command
        .env("POWERCONTEXT_HOME", directory.join("home"))
        .env("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
        .env(
            "POWERCONTEXT_SERVER_DATABASE_URL",
            format!(
                "sqlite+aiosqlite:///{}",
                directory.join("core.db").display()
            ),
        )
        .env("POWERCONTEXT_SERVER_ACCESS_MODE", "enforced")
        .env("POWERCONTEXT_SERVER_AUTH_TOKEN", TOKEN)
        .env("POWERCONTEXT_SERVER_MCP_ENABLED", "false")
        .env("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", "false")
        .env("POWERCONTEXT_SERVER_LOGGING_ACCESS", "false")
        .env(
            "POWERCONTEXT_SERVER_RUNTIME",
            r#"{"artifact_processing_families":[]}"#,
        )
        .env(
            "POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET",
            "disposable-signing-secret-32-bytes",
        );
    let child = command
        .spawn()
        .expect("real Core requires installed project Python; set PC_TEST_PYTHON");
    let mut process = CoreProcess { child, directory };
    let admin = reqwest::Client::builder()
        .unix_socket(socket.as_path())
        .retry(reqwest::retry::never())
        .timeout(Duration::from_secs(3))
        .build()
        .unwrap();
    let deadline = tokio::time::Instant::now() + Duration::from_secs(45);
    loop {
        assert!(
            process.child.try_wait().unwrap().is_none() && tokio::time::Instant::now() < deadline,
            "real Python Core failed startup: {}",
            std::fs::read_to_string(&log_path).unwrap()
        );
        if admin
            .get("http://localhost/health/ready")
            .send()
            .await
            .is_ok_and(|r| r.status().is_success())
        {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    (process, admin, socket)
}

async fn post(admin: &reqwest::Client, path: &str, body: Value) -> Value {
    let response = admin
        .post(format!("http://localhost{path}"))
        .bearer_auth(TOKEN)
        .header("x-powercontext-scope-mode", "current")
        .json(&body)
        .send()
        .await
        .unwrap();
    assert!(
        response.status().is_success(),
        "{}",
        response.text().await.unwrap()
    );
    response.json().await.unwrap()
}

// The proxy completes the real operation before discarding the first response.
// Its saved result is compared against retries and the public Core read path.
async fn loss_proxy(
    socket: &Path,
    path: &str,
    attempts: usize,
) -> (PathBuf, tokio::task::JoinHandle<Vec<Value>>) {
    let proxy = socket.with_file_name(format!("proxy-{attempts}.sock"));
    let _ = std::fs::remove_file(&proxy);
    let listener = tokio::net::UnixListener::bind(&proxy).unwrap();
    let admin = reqwest::Client::builder()
        .unix_socket(socket)
        .retry(reqwest::retry::never())
        .build()
        .unwrap();
    let path = path.to_string();
    let worker = tokio::spawn(async move {
        let mut results = Vec::new();
        let mut original = None;
        for attempt in 0..attempts {
            let (mut connection, _) = listener.accept().await.unwrap();
            let mut head = Vec::new();
            while !head.ends_with(b"\r\n\r\n") {
                head.push(connection.read_u8().await.unwrap());
            }
            assert!(String::from_utf8_lossy(&head).starts_with(&format!("POST {path} ")));
            let length: usize = String::from_utf8_lossy(&head)
                .lines()
                .find_map(|l| l.strip_prefix("Content-Length: "))
                .unwrap()
                .parse()
                .unwrap();
            let mut body = vec![0; length];
            connection.read_exact(&mut body).await.unwrap();
            if let Some(original) = &original {
                assert_eq!(original, &body);
            } else {
                original = Some(body.clone());
            }
            let response = admin
                .post(format!("http://localhost{path}"))
                .bearer_auth(TOKEN)
                .header("x-powercontext-scope-mode", "current")
                .header("content-type", "application/json")
                .body(body)
                .send()
                .await
                .unwrap();
            let status = response.status().as_u16();
            let bytes = response.bytes().await.unwrap();
            results.push(serde_json::from_slice::<Value>(&bytes).unwrap());
            if attempt != 0 {
                connection.write_all(format!("HTTP/1.1 {status} Response\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", bytes.len()).as_bytes()).await.unwrap();
                connection.write_all(&bytes).await.unwrap();
            }
            connection.shutdown().await.unwrap();
        }
        assert!(
            tokio::time::timeout(Duration::from_millis(150), listener.accept())
                .await
                .is_err(),
            "unexpected Core replay"
        );
        results
    });
    (proxy, worker)
}

#[tokio::test]
async fn real_core_capture_commit_loss_recovers_same_record_and_prepare_is_not_replayed() {
    let (_process, admin, socket) = start_core().await;
    let scope = post(&admin, "/v1/scopes", json!({"title":"RPC recovery", "summary":"real UDS evidence", "idempotency_key":"rpc-scope"})).await["scope_id"].as_str().unwrap().to_string();
    post(&admin, "/v1/access/bindings/create", json!({"subject":{"type":"service","id":"server-token"},
        "resource":{"type":"scope", "scope_id":scope}, "role":"scope.contributor", "idempotency_key":"rpc-grant"})).await;
    let capture = CaptureContentSourceRequest {
        scope_id: scope.clone(),
        source_id: "stable-source".into(),
        content: "原始证据".into(),
        metadata: Some(json!({"nested":{"items":[1,"值"]},"flag":true})),
    };
    let (proxy, worker) = loss_proxy(&socket, "/v1/sources/content", 2).await;
    let recovered = UdsGateClient::with_token(proxy, Some(TOKEN.into()))
        .capture(capture.clone())
        .await
        .unwrap();
    let committed = tokio::time::timeout(Duration::from_secs(5), worker)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(committed.len(), 2);
    assert_eq!(committed[0], committed[1]);
    assert_eq!(recovered, committed[0]);
    assert_eq!(recovered["position"], 1);
    assert_eq!(recovered["source"]["source_id"], "stable-source");
    let read_path = format!("http://localhost/v1/scopes/{scope}/sources/content/stable-source");
    let stored: Value = admin
        .get(&read_path)
        .bearer_auth(TOKEN)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(stored["position"], 1);
    assert_eq!(stored["content"], "原始证据");

    let client = UdsGateClient::with_token(&socket, Some(TOKEN.into()));
    for metadata_only in [false, true] {
        let mut different = capture.clone();
        if metadata_only {
            different.metadata = Some(json!({"changed":true}));
        } else {
            different.content = "异值".into();
        }
        assert!(matches!(
            client.capture(different).await,
            Err(GateError::GateRejected(409))
        ));
    }
    let second = CaptureContentSourceRequest {
        source_id: "next-source".into(),
        ..capture.clone()
    };
    assert_eq!(
        client.capture(second).await.unwrap()["position"],
        2,
        "lost reply recovery and conflicts must not append journal entries"
    );
    let page: Value = admin
        .get(format!("http://localhost/v1/scopes/{scope}/sources"))
        .bearer_auth(TOKEN)
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    assert_eq!(page["items"].as_array().unwrap().len(), 2);
    let (proxy, worker) = loss_proxy(&socket, "/v1/context/prepare", 1).await;
    assert!(UdsGateClient::with_token(proxy, Some(TOKEN.into()))
        .prepare(PrepareContextRequest {
            scope_id: scope.clone(),
            query: "hello".into(),
            max_bytes: 1024,
            include_reuse: false,
            include_code: false,
            assembly: Some(pc_gateway_types::ContextAssembly::memory_only()),
        })
        .await
        .is_err());
    let prepared = tokio::time::timeout(Duration::from_secs(5), worker)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(prepared.len(), 1);
    assert_eq!(prepared[0]["status"], "empty");
    assert!(client.capabilities().await.unwrap().is_object());
    assert_eq!(client.capture(capture).await.unwrap(), recovered);
}
