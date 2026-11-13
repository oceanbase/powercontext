// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::{AdmittedSend, NotSentReason, RawResponse, SendIndeterminateReason, SendOutcome};
use serde_json::{json, Value};
use std::{
    ffi::OsString,
    path::Path,
    process::Stdio,
    time::{Duration, Instant},
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};

fn forbidden_override() -> bool {
    [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ]
    .iter()
    .any(|name| std::env::var_os(name).is_some_and(|v| !v.is_empty()))
}

pub(super) async fn send(executable: &OsString, send: AdmittedSend) -> SendOutcome {
    if forbidden_override() {
        return SendOutcome::NotSent(NotSentReason::RejectedLocally);
    }
    let Ok(request) = serde_json::from_slice::<Value>(&send.payload) else {
        return SendOutcome::NotSent(NotSentReason::RejectedLocally);
    };
    let Some(model) = request["model"].as_str() else {
        return SendOutcome::NotSent(NotSentReason::RejectedLocally);
    };
    let Ok(directory) = tempfile::tempdir() else {
        return SendOutcome::NotSent(NotSentReason::RejectedLocally);
    };
    let status = run(executable, &["auth", "status"], directory.path(), None).await;
    if !status.is_ok_and(|value| value["authMethod"] == "claude.ai" && value["loggedIn"] != false) {
        return SendOutcome::NotSent(NotSentReason::RejectedLocally);
    }
    let start = Instant::now();
    // The admitted JSON is the exact prompt byte sequence, not an unchecked reconstruction.
    // Claude Code owns its internal HTTP and retries. This path is unavailable under enforcing Core.
    let output = run(
        executable,
        &[
            "--print",
            "--output-format",
            "json",
            "--model",
            model,
            "--tools",
            "",
            "--disallowedTools",
            "mcp__*",
            "--strict-mcp-config",
            "--mcp-config",
            "{\"mcpServers\":{}}",
            "--setting-sources",
            "",
            "--safe-mode",
            "--no-session-persistence",
            "--max-turns",
            "1",
        ],
        directory.path(),
        Some(&send.payload),
    )
    .await;
    let Ok(output) = output else {
        return SendOutcome::Indeterminate(SendIndeterminateReason::LostAfterHandoff);
    };
    let Some(text) = output["result"].as_str().filter(|_| {
        output["type"] == "result"
            && output["is_error"] != true
            && output.get("subtype").is_none_or(|value| value == "success")
    }) else {
        return SendOutcome::Indeterminate(SendIndeterminateReason::LostAfterHandoff);
    };
    let finish_reason = match output["stop_reason"].as_str() {
        Some("max_tokens" | "model_context_window_exceeded") => "length",
        Some("end_turn" | "stop_sequence") => "stop",
        None if output["stop_reason"].is_null() => "stop",
        _ => return SendOutcome::Indeterminate(SendIndeterminateReason::LostAfterHandoff),
    };
    let created = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|v| v.as_secs())
        .unwrap_or(0);
    let mut body = json!({"id":format!("claude-cli-{}",uuid::Uuid::new_v4()),"object":"chat.completion","created":created,"model":model,
        "choices":[{"index":0,"message":{"role":"assistant","content":text},"finish_reason":finish_reason}]});
    if let Some(usage) = output["usage"].as_object() {
        let mut normalized = serde_json::Map::new();
        for (source, target) in [
            ("input_tokens", "prompt_tokens"),
            ("output_tokens", "completion_tokens"),
        ] {
            if let Some(value) = usage.get(source) {
                normalized.insert(target.into(), value.clone());
            }
        }
        if let Some(cached) = usage.get("cache_read_input_tokens") {
            normalized.insert(
                "prompt_tokens_details".into(),
                json!({"cached_tokens":cached}),
            );
        }
        if usage.contains_key("cache_read_input_tokens")
            || usage.contains_key("cache_creation_input_tokens")
        {
            let optional = |name: &str| usage.get(name).map_or(Some(0), Value::as_u64);
            let total = usage
                .get("input_tokens")
                .and_then(Value::as_u64)
                .and_then(|input| {
                    input
                        .checked_add(optional("cache_read_input_tokens")?)?
                        .checked_add(optional("cache_creation_input_tokens")?)
                });
            if let Some(total) = total {
                normalized.insert("prompt_tokens".into(), json!(total));
            } else {
                normalized.remove("prompt_tokens");
            }
        }
        if let Some(total) = normalized
            .get("prompt_tokens")
            .and_then(Value::as_u64)
            .and_then(|input| input.checked_add(normalized.get("completion_tokens")?.as_u64()?))
        {
            normalized.insert("total_tokens".into(), json!(total));
        }
        body["usage"] = Value::Object(normalized);
    }
    let Ok(bytes) = serde_json::to_vec(&body) else {
        return SendOutcome::Indeterminate(SendIndeterminateReason::LostAfterHandoff);
    };
    if bytes.len() > pc_gateway_types::CONTEXT_CHECK_BODY_LIMIT {
        return SendOutcome::Indeterminate(SendIndeterminateReason::BufferLimitExceeded);
    }
    SendOutcome::Delivered(RawResponse {
        status: 200,
        content_type: "application/json".into(),
        usage: body.get("usage").cloned(),
        body: bytes.into(),
        response_first_byte_ms: None,
        response_complete_ms: Some(start.elapsed().as_millis() as u64),
    })
}

struct ProcessGroup {
    id: u32,
    active: bool,
}

impl Drop for ProcessGroup {
    fn drop(&mut self) {
        #[cfg(unix)]
        if self.active {
            // The child starts a new process group. Kill descendants holding stdout as well.
            unsafe {
                libc::kill(-(self.id as i32), libc::SIGKILL);
            }
        }
    }
}

async fn bounded_read(stream: impl AsyncRead + Unpin) -> Result<Vec<u8>, ()> {
    let mut bytes = Vec::new();
    stream
        .take(pc_gateway_types::CONTEXT_CHECK_BODY_LIMIT as u64 + 1)
        .read_to_end(&mut bytes)
        .await
        .map_err(|_| ())?;
    if bytes.len() > pc_gateway_types::CONTEXT_CHECK_BODY_LIMIT {
        return Err(());
    }
    Ok(bytes)
}

async fn run(
    executable: &OsString,
    arguments: &[&str],
    directory: &Path,
    input: Option<&[u8]>,
) -> Result<Value, ()> {
    let mut command = tokio::process::Command::new(executable);
    command
        .args(arguments)
        .current_dir(directory)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    #[cfg(unix)]
    command.process_group(0);
    let mut child = command.spawn().map_err(|_| ())?;
    let mut group = ProcessGroup {
        id: child.id().ok_or(())?,
        active: true,
    };
    let stdout = child.stdout.take().ok_or(())?;
    let mut stdin = child.stdin.take().ok_or(())?;
    let exchange = async {
        if let Some(input) = input {
            stdin.write_all(input).await.map_err(|_| ())?;
        }
        drop(stdin);
        let (output, status) = tokio::try_join!(bounded_read(stdout), async {
            child.wait().await.map_err(|_| ())
        })?;
        if !status.success() {
            return Err(());
        }
        serde_json::from_slice::<Value>(&output).map_err(|_| ())
    };
    let result = tokio::time::timeout(Duration::from_secs(180), exchange)
        .await
        .map_err(|_| ())?;
    if result.is_ok() {
        group.active = false;
    }
    result
}
