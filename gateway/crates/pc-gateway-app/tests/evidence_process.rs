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

//! Production composition in a real spawned Gateway, with local HTTP Core/provider fixtures.
use axum::{
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::io::{BufRead, Read};
use std::process::{Child, Command, Stdio};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};
use std::time::Duration;

// First-event usage and the only content delta share a leading-BOM event.
const BOM_SSE_RESPONSE: &str = "\u{feff}data: {\"choices\":[{\"delta\":{\"content\":\"SYNTHETIC_RESPONSE_SECRET\"}}],\"usage\":{\"prompt_tokens\":101,\"completion_tokens\":13,\"prompt_tokens_details\":{\"cached_tokens\":61}}}\r\n\r\ndata: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\r\n\r\ndata: [DONE]\r\n\r\n";

struct Gateway(Child);
impl Drop for Gateway {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn production_jsonl_and_explicit_noop_keep_real_requests_usable() {
    let fixture = Router::new()
        .route("/v1/capabilities", get(|| async { Json(json!({"context_protection": {
            "check_versions": ["powercontext.context-check.v1"], "payload_versions": ["powercontext.model-json-payload.v1"], "enforcing":true,
            "reuse_versions": [], "reuse_families": [],
            "limits": {"payload_bytes": 1048576, "binding_bytes": 8192, "prepared_contexts": 16,
                "dependencies": 256, "send_seconds": 60, "correlate_seconds": 300}
        }})) }))
        .route("/v1/context/check", post(|Json(request): Json<Value>| async move { Json(json!({
            "schema":"powercontext.context-check.v1", "phase":request["phase"], "decision":"allow",
            "reason_codes":[], "coverage":{"profile":"text-and-tools.v1", "complete":true},
            "policy_version":"fixture", "valid_until":"2999-01-01T00:00:00Z", "binding":"fixture-binding"
        })) }))
        .route("/v1/chat/completions", post(|Json(request): Json<Value>| async move {
            if request["stream"] == true {
                ([("content-type", "Text/Event-Stream; charset=utf-8")], BOM_SSE_RESPONSE).into_response()
            } else {
                Json(json!({
                    "choices":[{"message":{"role":"assistant", "content":"SYNTHETIC_RESPONSE_SECRET"}}],
                    "usage":{"prompt_tokens":101, "completion_tokens":13, "prompt_tokens_details":{"cached_tokens":61}}
                })).into_response()
            }
        }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!(
        "http://SYNTHETIC_CORE_USER:SYNTHETIC_CORE_PASSWORD@{}/SYNTHETIC_PRIVATE_PATH",
        listener.local_addr().unwrap()
    );
    let fixture = Router::new().nest("/SYNTHETIC_PRIVATE_PATH", fixture);
    let fixture_task = tokio::spawn(async move {
        axum::serve(listener, fixture).await.unwrap();
    });
    let client = reqwest::Client::new();
    for disabled in [false, true] {
        let port = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = port.local_addr().unwrap();
        drop(port);
        let mut command = Command::new(env!("CARGO_BIN_EXE_pc-gateway"));
        for (key, _) in std::env::vars_os() {
            if key.to_string_lossy().starts_with("PC_GATEWAY_") {
                command.env_remove(key);
            }
        }
        command
            .env("PC_GATEWAY_CORE_BASE", &base)
            .env("PC_GATEWAY_LISTEN", address.to_string())
            .env(
                "PC_GATEWAY_UPSTREAM_URL",
                format!("{base}/v1/chat/completions"),
            )
            .env("PC_GATEWAY_API_KEY", "SYNTHETIC_GATEWAY_KEY")
            .env("PC_GATEWAY_UPSTREAM_API_KEY", "SYNTHETIC_PROVIDER_KEY")
            .env("PC_GATEWAY_INJECTION", "SYNTHETIC_INVALID_INJECTION_VALUE")
            .env("RUST_LOG", "info")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if disabled {
            command.env("PC_GATEWAY_EVIDENCE", "off");
        }
        let mut gateway = Gateway(command.spawn().unwrap());
        let stdout = gateway.0.stdout.take().unwrap();
        let mut stderr = gateway.0.stderr.take().unwrap();
        let lines = Arc::new(AtomicUsize::new(0));
        let read_lines = lines.clone();
        let output = std::thread::spawn(move || {
            std::io::BufReader::new(stdout)
                .lines()
                .map(|line| {
                    let line = line.unwrap();
                    read_lines.fetch_add(1, Ordering::Relaxed);
                    line
                })
                .collect::<Vec<_>>()
        });
        let errors = std::thread::spawn(move || {
            let mut text = String::new();
            stderr.read_to_string(&mut text).unwrap();
            text
        });
        let url = format!("http://{address}");
        tokio::time::timeout(Duration::from_secs(10), async {
            loop {
                if client.get(format!("{url}/healthz")).send().await.is_ok() {
                    break;
                }
                assert!(
                    gateway.0.try_wait().unwrap().is_none(),
                    "Gateway exited during startup"
                );
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        let count = if disabled { 100 } else { 1000 };
        for i in 0..count {
            let stream = i % 2 == 1;
            let response = client.post(format!("{url}/gateway/v1/chat/completions"))
                .bearer_auth("SYNTHETIC_GATEWAY_KEY")
                .header("x-powercontext-request-id", "SYNTHETIC_REQUEST_SECRET")
                .json(&json!({"model":"SYNTHETIC_MODEL", "messages":[{"role":"user", "content":"SYNTHETIC_PROMPT_SECRET"}], "stream":stream}))
                .send().await.unwrap();
            assert_eq!(response.status(), 200);
            if stream {
                assert_eq!(
                    response.headers()["content-type"],
                    "Text/Event-Stream; charset=utf-8"
                );
                assert_eq!(
                    response.bytes().await.unwrap().as_ref(),
                    BOM_SSE_RESPONSE.as_bytes()
                );
            } else {
                assert_eq!(
                    response.json::<Value>().await.unwrap()["usage"]["prompt_tokens"],
                    101
                );
            }
        }
        if !disabled {
            tokio::time::timeout(Duration::from_secs(5), async {
                while lines.load(Ordering::Relaxed) < count * 6 {
                    tokio::task::yield_now().await;
                }
            })
            .await
            .unwrap();
        }
        drop(gateway);
        let output = output.join().unwrap();
        let errors = errors.join().unwrap();
        assert!(!errors.contains("SYNTHETIC_"));
        if disabled {
            assert!(output.is_empty());
            assert!(errors.contains("evidence disabled"));
        } else {
            assert_eq!(output.len(), count * 6);
            let mut call_ids = std::collections::BTreeSet::new();
            let mut content_ids = std::collections::BTreeSet::new();
            for line in output {
                assert!(!line.contains("SYNTHETIC_"));
                let record: Value = serde_json::from_str(&line).unwrap();
                assert_eq!(record["loss"]["dropped"], 0);
                if record["event"] == "delivery"
                    && record["evidence"]["boundary"] == "first_model_content"
                {
                    assert!(content_ids
                        .insert(record["evidence"]["call_id"].as_str().unwrap().to_owned()));
                }
                if record["event"] == "call" {
                    let evidence = &record["evidence"];
                    assert_eq!(record["operation"], "gateway.model_call");
                    assert_eq!(record["unit"], "call");
                    assert_eq!(record["outcome"], "success");
                    assert_eq!(record["duration_ms"], evidence["duration_ms"]);
                    assert_eq!(evidence["terminal"], "release_authorized");
                    assert_eq!(evidence["usage"]["input"]["tokens"], 101);
                    assert_eq!(evidence["usage"]["output"]["tokens"], 13);
                    assert_eq!(evidence["usage"]["cache_read"]["tokens"], 61);
                    assert!(evidence["usage"]["cache_write"]["tokens"].is_null());
                    assert!(evidence["upstream_ttft_ms"].is_null());
                    call_ids.insert(evidence["call_id"].as_str().unwrap().to_owned());
                }
            }
            assert_eq!(call_ids.len(), count);
            assert_eq!(content_ids, call_ids);
        }
    }
    fixture_task.abort();
}

#[test]
fn rejected_startup_does_not_expose_uds_or_steering_paths() {
    let mut command = Command::new(env!("CARGO_BIN_EXE_pc-gateway"));
    for (key, _) in std::env::vars_os() {
        if key.to_string_lossy().starts_with("PC_GATEWAY_") {
            command.env_remove(key);
        }
    }
    let missing = std::env::temp_dir()
        .join(format!("SYNTHETIC_PRIVATE_STEERING_{}", std::process::id()))
        .join("missing.txt");
    let output = command
        .env("PC_GATEWAY_CORE_UDS", "/SYNTHETIC_PRIVATE_SOCKET/core.sock")
        .env("PC_GATEWAY_STEERING_FILE", missing)
        .env("RUST_LOG", "info")
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    let errors = String::from_utf8(output.stderr).unwrap();
    assert!(errors.contains("core link: UDS"));
    assert!(errors.contains("proxy: forwarding"));
    assert!(errors.contains("file cannot be read"));
    assert!(!errors.contains("SYNTHETIC_"));
}
