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
#![cfg(unix)]

use axum::{routing::get, Json, Router};
use serde_json::json;
use std::{os::unix::fs::PermissionsExt, process::Stdio, time::Duration};

#[tokio::test]
async fn core_bound_egress_never_starts_a_model_cli() {
    for enforcing in [Some(true), Some(false), None] {
        let directory = std::env::temp_dir().join(format!(
            "pc-cli-enforcement-{}-{enforcing:?}",
            std::process::id()
        ));
        std::fs::create_dir_all(&directory).unwrap();
        let marker = directory.join("started");
        let executable = directory.join("claude");
        std::fs::write(
            &executable,
            format!("#!/bin/sh\ntouch '{}'\n", marker.display()),
        )
        .unwrap();
        std::fs::set_permissions(&executable, std::fs::Permissions::from_mode(0o700)).unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let app=Router::new().route("/v1/capabilities",get(move ||async move {
            Json(if let Some(enforcing) = enforcing {json!({"context_protection": {
                "check_versions":["powercontext.context-check.v1"],"payload_versions":["powercontext.model-json-payload.v1"],
                "reuse_versions":[],"reuse_families":[],"enforcing":enforcing,
                "limits":{"payload_bytes":1048576,"binding_bytes":8192,"prepared_contexts":16,
                    "dependencies":256,"send_seconds":60,"correlate_seconds":300}
            }})} else {json!({})})
        }));
        let peer = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let mut command = tokio::process::Command::new(env!("CARGO_BIN_EXE_pc-gateway"));
        command
            .env_clear()
            .env("PC_GATEWAY_CORE_BASE", base)
            .env("PC_GATEWAY_LISTEN", "127.0.0.1:0")
            .env("PC_GATEWAY_SUBSCRIPTION", "claude")
            .env("PC_GATEWAY_API_KEY", "gateway-key")
            .env("PC_GATEWAY_CORE_TOKEN", "core-key")
            .env("CLAUDE_CLI_EXECUTABLE", &executable)
            .env("PC_GATEWAY_EVIDENCE", "off")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let result = tokio::time::timeout(Duration::from_secs(5), command.output())
            .await
            .unwrap()
            .unwrap();
        peer.abort();
        assert_eq!(result.status.code(), Some(2));
        assert!(String::from_utf8(result.stderr)
            .unwrap()
            .contains("Claude CLI has unobservable internal sends"));
        assert!(!marker.exists());
        std::fs::remove_dir_all(directory).unwrap();
    }
}
