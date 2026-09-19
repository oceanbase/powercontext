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

//! Allowlisted projections of local CLI diagnostics; raw output never crosses IPC.
use crate::error::SafeError;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use ts_rs::TS;

pub const OUTPUT_LIMIT: usize = 256 * 1024;
#[derive(Clone, Copy, Deserialize, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum DiagnosticKind {
    Service,
    Integrations,
}
impl DiagnosticKind {
    pub fn arguments(self) -> [&'static str; 3] {
        match self {
            Self::Service => ["service", "status", "--json"],
            Self::Integrations => ["doctor", "integrations", "--json"],
        }
    }
}
#[derive(Clone, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DiagnosticItem {
    // Both strings are selected from the static vocabulary below, never arbitrary CLI text.
    pub field: String,
    pub status: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct HostDiagnostic {
    pub host: String,
    pub presence: String,
    pub checks: Vec<DiagnosticItem>,
}
#[derive(Clone, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DiagnosticReport {
    pub checked_at: u64,
    pub exit_code: i32,
    pub items: Vec<DiagnosticItem>,
    pub hosts: Vec<HostDiagnostic>,
}
fn selected(value: &Value, allowed: &[&str]) -> Result<String, SafeError> {
    let value = value.as_str().ok_or(SafeError::InvalidResponse)?;
    allowed
        .contains(&value)
        .then(|| value.to_owned())
        .ok_or(SafeError::InvalidResponse)
}
/// Exit 1 can carry valid, useful unhealthy diagnostics. Process failure and malformed JSON remain distinct.
pub fn project(
    kind: DiagnosticKind,
    output: &[u8],
    exit_code: i32,
) -> Result<DiagnosticReport, SafeError> {
    if output.len() > OUTPUT_LIMIT {
        return Err(SafeError::ResponseTooLarge);
    }
    let value: Value = serde_json::from_slice(output).map_err(|_| SafeError::InvalidResponse)?;
    if !value.is_object() {
        return Err(SafeError::InvalidResponse);
    }
    let mut report = DiagnosticReport {
        checked_at: std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
        exit_code,
        items: vec![],
        hosts: vec![],
    };
    match kind {
        DiagnosticKind::Service => {
            for (field, allowed) in [
                ("support", &["supported", "unsupported"][..]),
                (
                    "registration",
                    &["installed", "not_installed", "invalid", "unknown"][..],
                ),
                (
                    "definition",
                    &["current", "stale", "missing_executable", "unknown"][..],
                ),
                (
                    "manager_ownership",
                    &["not_loaded", "owned", "foreign", "unknown"][..],
                ),
                ("manager", &["active", "inactive", "failed", "unknown"][..]),
                ("server_liveness", &["live", "unreachable", "unknown"][..]),
            ] {
                report.items.push(DiagnosticItem {
                    field: field.into(),
                    status: selected(&value[field], allowed)?,
                });
            }
        }
        DiagnosticKind::Integrations => {
            let status = selected(&value["status"], &["ok", "failed"])?;
            if value["ok"].as_bool() != Some(status == "ok") {
                return Err(SafeError::InvalidResponse);
            }
            report.items.push(DiagnosticItem {
                field: "integrations".into(),
                status,
            });
            let hosts = value["hosts"]
                .as_object()
                .ok_or(SafeError::InvalidResponse)?;
            if hosts.len() > 8 {
                return Err(SafeError::InvalidResponse);
            }
            for (name, host) in hosts {
                if ![
                    "codex",
                    "claude-code",
                    "dsh",
                    "openclaw",
                    "opencode",
                    "pi",
                    "hermes",
                    "workbuddy",
                ]
                .contains(&name.as_str())
                {
                    return Err(SafeError::InvalidResponse);
                }
                let mut row = HostDiagnostic {
                    host: name.clone(),
                    presence: selected(&host["presence"], &["present", "missing"])?,
                    checks: vec![],
                };
                let fields = host.as_object().ok_or(SafeError::InvalidResponse)?;
                for (field, check) in fields {
                    if field == "presence" {
                        continue;
                    }
                    if ![
                        "codex",
                        "claude_code",
                        "dsh",
                        "openclaw",
                        "opencode",
                        "pi",
                        "hermes",
                        "hooks",
                        "plugin",
                        "package",
                        "skill",
                        "settings",
                        "mcp",
                        "transport",
                    ]
                    .contains(&field.as_str())
                    {
                        return Err(SafeError::InvalidResponse);
                    }
                    let status =
                        selected(&check["status"], &["ok", "degraded", "failed", "skipped"])?;
                    if check["ok"].as_bool() != Some(status == "ok") {
                        return Err(SafeError::InvalidResponse);
                    }
                    row.checks.push(DiagnosticItem {
                        field: field.clone(),
                        status,
                    });
                }
                if row.checks.is_empty() {
                    return Err(SafeError::InvalidResponse);
                }
                report.hosts.push(row);
            }
        }
    }
    Ok(report)
}

#[cfg(windows)]
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CliRegistration {
    executable: std::path::PathBuf,
    sha256: String,
    version: String,
    source: String,
}
/// Native-only registration. A renderer cannot supply a program, path, arguments, or environment.
#[cfg(windows)]
pub struct LocalDiagnostics {
    registration: std::path::PathBuf,
    running: std::sync::Arc<tokio::sync::Semaphore>,
}
#[cfg(windows)]
impl LocalDiagnostics {
    pub fn new(registration: std::path::PathBuf) -> Self {
        Self {
            registration,
            running: std::sync::Arc::new(tokio::sync::Semaphore::new(1)),
        }
    }
    pub async fn run(&self, kind: DiagnosticKind) -> Result<DiagnosticReport, SafeError> {
        use std::sync::{
            Arc,
            atomic::{AtomicBool, Ordering},
        };
        let permit = self
            .running
            .clone()
            .try_acquire_owned()
            .map_err(|_| SafeError::Busy)?;
        struct Cancel(Arc<AtomicBool>);
        impl Drop for Cancel {
            fn drop(&mut self) {
                self.0.store(true, Ordering::Relaxed);
            }
        }
        let cancelled = Cancel(Arc::new(AtomicBool::new(false)));
        let flag = cancelled.0.clone();
        let registration = self.registration.clone();
        tauri::async_runtime::spawn_blocking(move || {
            let _permit = permit;
            execute(&registration, kind, flag)
        })
        .await
        .map_err(|_| SafeError::Storage)?
    }
}
#[cfg(windows)]
fn execute(
    registration: &std::path::Path,
    kind: DiagnosticKind,
    cancelled: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Result<DiagnosticReport, SafeError> {
    use sha2::{Digest, Sha256};
    use std::{io::Read, os::windows::fs::OpenOptionsExt, time::Duration};
    let file = std::fs::File::open(registration).map_err(|_| SafeError::NotFound)?;
    let mut bytes = Vec::new();
    file.take(16385)
        .read_to_end(&mut bytes)
        .map_err(|_| SafeError::Storage)?;
    if bytes.len() > 16384 {
        return Err(SafeError::InvalidInput);
    }
    let config: CliRegistration =
        serde_json::from_slice(&bytes).map_err(|_| SafeError::InvalidInput)?;
    // This is an explicit local-installation pin, not publisher-signature verification.
    if config.source != "explicit_local_installation"
        || !(config.version == "1.0.1" || config.version.starts_with("1.0.1.dev"))
        || config.version.len() > 128
        || !config
            .version
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'+' | b'-'))
        || config.sha256.len() != 64
        || !config.sha256.bytes().all(|v| v.is_ascii_hexdigit())
        || !config.executable.is_absolute()
        || config.executable.file_name().and_then(|v| v.to_str()) != Some("powercontext.exe")
    {
        return Err(SafeError::CompatibilityUnverified);
    }
    // Keep a read-only, no-delete/no-write-sharing handle open through both invocations.
    let mut executable = std::fs::OpenOptions::new()
        .read(true)
        .share_mode(1)
        .open(&config.executable)
        .map_err(|_| SafeError::NotFound)?;
    if executable.metadata().map_err(|_| SafeError::Storage)?.len() > 64 * 1024 * 1024 {
        return Err(SafeError::InvalidInput);
    }
    let mut digest = Sha256::new();
    let mut buf = [0; 8192];
    loop {
        let n = executable.read(&mut buf).map_err(|_| SafeError::Storage)?;
        if n == 0 {
            break;
        }
        digest.update(&buf[..n]);
    }
    if format!("{:x}", digest.finalize()) != config.sha256.to_ascii_lowercase() {
        return Err(SafeError::CompatibilityUnverified);
    }
    // A fixed version probe validates the pinned CLI before either diagnostic command runs.
    let version = crate::diagnostic_process::run(
        &config.executable,
        &["--version"],
        Duration::from_secs(15),
        cancelled.clone(),
    )?;
    if version.exit_code != 0
        || std::str::from_utf8(&version.stdout).map(str::trim) != Ok(config.version.as_str())
    {
        return Err(SafeError::CompatibilityUnverified);
    }
    let timeout = match kind {
        DiagnosticKind::Service => 20,
        DiagnosticKind::Integrations => 60,
    };
    let result = crate::diagnostic_process::run(
        &config.executable,
        &kind.arguments(),
        Duration::from_secs(timeout),
        cancelled,
    )?;
    project(kind, &result.stdout, result.exit_code)
}

pub struct DiagnosticHost {
    #[cfg(windows)]
    pub local: Option<LocalDiagnostics>,
}
impl DiagnosticHost {
    pub fn new(directory: Option<std::path::PathBuf>) -> Self {
        #[cfg(windows)]
        {
            Self {
                local: directory
                    .map(|path| LocalDiagnostics::new(path.join("diagnostic-cli.json"))),
            }
        }
        #[cfg(not(windows))]
        {
            let _ = directory;
            Self {}
        }
    }
}
