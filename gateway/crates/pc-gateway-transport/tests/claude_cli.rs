// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
#![cfg(unix)]

use pc_gateway_transport::{
    AdmittedSend, GatewayTransport, ReqwestTransport, SendOutcome, Transport,
};
use serde_json::{json, Value};
use std::{os::unix::fs::PermissionsExt, path::Path, time::Duration};

fn quote(path: &Path) -> String {
    format!("'{}'", path.to_str().unwrap().replace('\'', "'\\''"))
}

fn script(directory: &Path, body: &str) -> std::path::PathBuf {
    let path = directory.join("claude-test");
    std::fs::write(&path,format!("#!/bin/sh\nif [ \"$1\" = auth ]; then printf '%s' '{{\"authMethod\":\"claude.ai\"}}'; exit 0; fi\n{body}\n")).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
    path
}

fn admitted() -> AdmittedSend {
    AdmittedSend {binding:"approved-prompt".into(),payload:serde_json::to_vec(&json!({
        "model":"sonnet","messages":[{"role":"user","content":"ordinary request"}],"stream":false
    })).unwrap().into(),target_url:"claude-cli://execute".into(),headers:vec![],stream:false}
}

#[tokio::test]
async fn goose_original_usage_and_errors_with_print_mode_adapter() {
    // Only usage and errors are upstream literals. The print result wrapper is
    // explicitly synthetic, and exercises the real subprocess path.
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/oauth/upstream/provider-vectors.json");
    let vectors: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let vector = &vectors["claude"];
    let mut result = vector["adapter_wrapper"].clone();
    result["usage"] = vector["usage_input"].clone();
    let mut outputs = vec![(
        result.clone(),
        Some((
            vector["expected"]["input_tokens"].clone(),
            vector["expected"]["output_tokens"].clone(),
            "stop",
        )),
    )];
    // Anthropic cache read/write counts are separate from uncached input.
    let mut cached = result.clone();
    cached["usage"] = json!({"input_tokens":10,"cache_read_input_tokens":100,"cache_creation_input_tokens":20,"output_tokens":3});
    outputs.push((cached, Some((json!(130), json!(3), "stop"))));
    for stop_reason in ["max_tokens", "model_context_window_exceeded"] {
        let mut truncated = result.clone();
        truncated["stop_reason"] = json!(stop_reason);
        outputs.push((
            truncated,
            Some((
                vector["expected"]["input_tokens"].clone(),
                vector["expected"]["output_tokens"].clone(),
                "length",
            )),
        ));
    }
    outputs.extend(
        vector["errors"]
            .as_array()
            .unwrap()
            .iter()
            .cloned()
            .map(|v| (v, None)),
    );
    outputs.push((
        json!({"type":"result","subtype":"error_max_turns","result":"partial"}),
        None,
    ));
    outputs.push((json!({"type":"error"}), None));
    for (value, expected_usage) in outputs {
        let output = serde_json::to_string(&value).unwrap();
        let directory = tempfile::tempdir().unwrap();
        let prompt = directory.path().join("prompt");
        let arguments = directory.path().join("arguments");
        let fixture = directory.path().join("result.json");
        std::fs::write(&fixture, output).unwrap();
        let path = script(
            directory.path(),
            &format!(
                "printf '%s\\n' \"$@\" > {}\ncat > {}\ncat {}",
                quote(&arguments),
                quote(&prompt),
                quote(&fixture)
            ),
        );
        let transport = GatewayTransport::new(ReqwestTransport::default())
            .with_claude(Some(path.into_os_string()));
        let send = admitted();
        let expected = send.payload.clone();
        let result = transport.send(send).await;
        assert_eq!(std::fs::read(prompt).unwrap(), expected.as_ref());
        let arguments = std::fs::read_to_string(arguments).unwrap();
        assert!(arguments.contains("--tools\n\n"));
        assert!(arguments.contains("--max-turns\n1"));
        assert!(arguments.contains("--no-session-persistence"));
        assert!(arguments.contains("--strict-mcp-config"));
        if let Some((input, output, finish_reason)) = expected_usage {
            let SendOutcome::Delivered(raw) = result else {
                panic!("original result lost");
            };
            let body: Value = serde_json::from_slice(&raw.body).unwrap();
            assert_eq!(
                body["choices"][0]["message"]["content"],
                vector["adapter_wrapper"]["result"]
            );
            assert_eq!(body["usage"]["prompt_tokens"], input);
            assert_eq!(body["usage"]["completion_tokens"], output);
            assert_eq!(
                body["usage"]["total_tokens"],
                input.as_u64().unwrap() + output.as_u64().unwrap()
            );
            assert_eq!(body["choices"][0]["finish_reason"], finish_reason);
        } else {
            assert!(matches!(result, SendOutcome::Indeterminate(_)));
        }
    }
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn cancellation_terminates_the_real_cli_process_group() {
    let directory = tempfile::tempdir().unwrap();
    let parent = directory.path().join("parent");
    let descendant = directory.path().join("descendant");
    let path = script(
        directory.path(),
        &format!(
            "cat > /dev/null\nprintf '%s' \"$$\" > {}\nsleep 120 &\nprintf '%s' \"$!\" > {}\nwait",
            quote(&parent),
            quote(&descendant)
        ),
    );
    let transport =
        GatewayTransport::new(ReqwestTransport::default()).with_claude(Some(path.into_os_string()));
    let task = tokio::spawn(async move { transport.send(admitted()).await });
    tokio::time::timeout(Duration::from_secs(5), async {
        while std::fs::read_to_string(&descendant)
            .unwrap_or_default()
            .is_empty()
        {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
    let ids: Vec<u32> = [parent, descendant]
        .iter()
        .map(|path| std::fs::read_to_string(path).unwrap().parse().unwrap())
        .collect();
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let active = ids.iter().any(|id| {
                std::fs::read_to_string(format!("/proc/{id}/stat")).is_ok_and(|stat| {
                    stat.rsplit_once(')')
                        .is_some_and(|(_, rest)| !rest.trim_start().starts_with('Z'))
                })
            });
            if !active {
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .unwrap();
}
