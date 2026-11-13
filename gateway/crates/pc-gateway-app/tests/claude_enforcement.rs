// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#![cfg(unix)]

use axum::{routing::get, Json, Router};
use serde_json::json;
use std::{os::unix::fs::PermissionsExt, process::Stdio, time::Duration};

#[tokio::test]
async fn enforcing_or_unknown_core_never_starts_a_model_cli() {
    for enforcing in [Some(true), None] {
        let directory = std::env::temp_dir().join(format!(
            "pc-cli-enforcement-{}-{}",
            std::process::id(),
            enforcing.is_some()
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
            Json(if enforcing.is_some() {json!({"context_protection": {
                "check_versions":["powercontext.context-check.v1"],"payload_versions":["powercontext.model-json-payload.v1"],
                "reuse_versions":[],"reuse_families":[],"enforcing":true,
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
            .contains("Claude CLI requires confirmed non-enforcing Core"));
        assert!(!marker.exists());
        std::fs::remove_dir_all(directory).unwrap();
    }
}
