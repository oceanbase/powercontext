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

//! A loopback Core approval must come from that listener, never from an ambient HTTP proxy.
use axum::{routing::post, Json, Router};
use pc_gateway_core::{GateClient, HttpGateClient};
use pc_gateway_types::{ContextCheckRequest, ModelJsonPayload};
use serde_json::json;
use std::{
    collections::BTreeMap,
    process::Command,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
};
use tokio::net::TcpListener;

#[test]
fn loopback_core_ignores_ambient_http_proxy() {
    // The proxy environment is process-wide; isolate this test from concurrently running tests.
    let result = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "proxy_probe_child", "--nocapture"])
        .env("PC_CORE_PROXY_PROBE", "1")
        .env_remove("HTTP_PROXY")
        .env_remove("http_proxy")
        .env_remove("HTTPS_PROXY")
        .env_remove("https_proxy")
        .env_remove("ALL_PROXY")
        .env_remove("all_proxy")
        .env_remove("NO_PROXY")
        .env_remove("no_proxy")
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
}

#[test]
fn proxy_probe_child() {
    if std::env::var_os("PC_CORE_PROXY_PROBE").is_none() {
        return;
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let core = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let proxy = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let core_url = format!("http://{}", core.local_addr().unwrap());
        let proxy_url = format!("http://{}", proxy.local_addr().unwrap());
        let core_hits = Arc::new(AtomicUsize::new(0));
        let proxy_hits = Arc::new(AtomicUsize::new(0));
        let hits = core_hits.clone();
        let core_task = tokio::spawn(async move {
            axum::serve(
                core,
                Router::new().route(
                    "/v1/context/check",
                    post(move || {
                        hits.fetch_add(1, Ordering::SeqCst);
                        async { Json(verdict("http://approved.example/v1/chat/completions")) }
                    }),
                ),
            )
            .await
            .unwrap();
        });
        let hits = proxy_hits.clone();
        let proxy_task = tokio::spawn(async move {
            axum::serve(
                proxy,
                Router::new().fallback(move || {
                    hits.fetch_add(1, Ordering::SeqCst);
                    async { Json(verdict("http://attacker.example/v1/chat/completions")) }
                }),
            )
            .await
            .unwrap();
        });

        std::env::set_var("HTTP_PROXY", &proxy_url);
        std::env::set_var("ALL_PROXY", &proxy_url);
        let client = HttpGateClient::with_token(core_url, Some("core-service-token".into()));
        let checked = client
            .check(ContextCheckRequest::BeforeSend {
                scope_id: "test".into(),
                target_profile: "test".into(),
                payload: ModelJsonPayload::new(BTreeMap::new(), r#"{"model":"test"}"#.into()),
                prepared_contexts: vec![],
            })
            .await
            .unwrap();
        assert_eq!(
            checked.target_endpoint.as_deref(),
            Some("http://approved.example/v1/chat/completions")
        );
        assert_eq!(
            core_hits.load(Ordering::SeqCst),
            1,
            "Core must issue the approval"
        );
        assert_eq!(
            proxy_hits.load(Ordering::SeqCst),
            0,
            "proxy must not see privileged Core requests"
        );
        core_task.abort();
        proxy_task.abort();
    });
}

fn verdict(endpoint: &str) -> serde_json::Value {
    json!({
        "schema": "powercontext.context-check.v1",
        "phase": "before_send",
        "decision": "allow",
        "reason_codes": [],
        "coverage": {"profile": "test", "complete": true},
        "policy_version": "test",
        "valid_until": "2999-01-01T00:00:00Z",
        "binding": "test-binding",
        "target_endpoint": endpoint,
        "target_protocol": "openai-chat"
    })
}
