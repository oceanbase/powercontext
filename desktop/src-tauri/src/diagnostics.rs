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
    pub fn arguments(self) -> &'static [&'static str] {
        match self {
            Self::Service => &["service", "status", "--json"],
            Self::Integrations => &[
                "doctor",
                "codex",
                "claude-code",
                "dsh",
                "openclaw",
                "opencode",
                "pi",
                "hermes",
                "workbuddy",
                "kimi-code",
                "zcode",
                "--json",
            ],
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
    pub status: String,
    pub checks: Vec<DiagnosticItem>,
}
#[derive(Clone, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DiagnosticReport {
    pub checked_at: u64,
    pub exit_code: i32,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub cli_version: Option<String>,
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
        cli_version: None,
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
            let ok = value["ok"].as_bool().ok_or(SafeError::InvalidResponse)?;
            let status = if ok { "ok" } else { "failed" }.to_owned();
            report.items.push(DiagnosticItem {
                field: "integrations".into(),
                status,
            });
            let hosts = value["hosts"]
                .as_object()
                .ok_or(SafeError::InvalidResponse)?;
            if hosts.len() > 10 {
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
                    "kimi-code",
                    "zcode",
                ]
                .contains(&name.as_str())
                {
                    return Err(SafeError::InvalidResponse);
                }
                let mut row = HostDiagnostic {
                    host: name.clone(),
                    status: selected(&host["status"], &["ok", "degraded", "failed"])?,
                    checks: vec![],
                };
                if host["ok"].as_bool() != Some(row.status == "ok") {
                    return Err(SafeError::InvalidResponse);
                }
                let fields = match host.get("checks") {
                    Some(checks) => checks.as_object().ok_or(SafeError::InvalidResponse)?,
                    None if row.status == "failed" => {
                        report.hosts.push(row);
                        continue;
                    }
                    None => return Err(SafeError::InvalidResponse),
                };
                for (field, check) in fields {
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
                        "authorization",
                        "mcp_configuration",
                        "mcp_tools",
                        "mcp_full_profile",
                        "kimi_code",
                        "node",
                        "hook",
                        "configuration",
                        "probe",
                        "credentials",
                        "scope",
                        "server",
                        "runtime",
                        "verification",
                        "mcp_session",
                        "protected_api",
                        "scope_probe",
                        "prepare_probe",
                        "zcode",
                        "provider",
                        "command_enabled",
                        "command_plugin",
                        "service_support",
                        "service_registration",
                        "service_definition",
                        "service_manager",
                        "server_liveness",
                        "server_readiness",
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

#[cfg(any(windows, target_os = "macos", test))]
fn cli_version_from_output(output: &[u8], exit_code: i32) -> Option<String> {
    if exit_code != 0 {
        return None;
    }
    let version = std::str::from_utf8(output).ok()?.trim();
    // Version is bounded display metadata; diagnostic responses establish compatibility.
    (version.len() <= 128
        && version.as_bytes().first().is_some_and(u8::is_ascii_digit)
        && version
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'+' | b'-' | b'_')))
    .then(|| version.to_owned())
}

#[cfg(any(windows, target_os = "macos"))]
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct CliRegistration {
    executable: std::path::PathBuf,
    sha256: String,
    #[serde(default, rename = "version")]
    _legacy_version: Option<serde::de::IgnoredAny>,
    source: String,
}
#[cfg(any(windows, target_os = "macos"))]
#[derive(Default)]
struct DiagnosticLifecycle {
    state: std::sync::Mutex<DiagnosticLifecycleState>,
    completed: std::sync::Condvar,
}
#[cfg(any(windows, target_os = "macos"))]
#[derive(Default)]
struct DiagnosticLifecycleState {
    closed: bool,
    active: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
}
#[cfg(any(windows, target_os = "macos"))]
struct DiagnosticCompletion(std::sync::Arc<DiagnosticLifecycle>);
#[cfg(any(windows, target_os = "macos"))]
impl Drop for DiagnosticCompletion {
    fn drop(&mut self) {
        let mut state = self.0.state.lock().unwrap();
        state.active = None;
        self.0.completed.notify_all();
    }
}
/// Native-only registration. A renderer cannot supply a program, path, arguments, or environment.
#[cfg(any(windows, target_os = "macos"))]
pub struct LocalDiagnostics {
    registration: std::path::PathBuf,
    lifecycle: std::sync::Arc<DiagnosticLifecycle>,
}
#[cfg(any(windows, target_os = "macos"))]
impl LocalDiagnostics {
    pub fn new(registration: std::path::PathBuf) -> Self {
        Self {
            registration,
            lifecycle: std::sync::Arc::new(DiagnosticLifecycle::default()),
        }
    }
    /// Stop active diagnostic work before the application exits.
    pub fn shutdown(&self) {
        use std::sync::atomic::Ordering;
        let mut state = self.lifecycle.state.lock().unwrap();
        // Close admission before cancelling, including a worker not yet started
        // by spawn_blocking. Completion follows process-group cleanup and reap.
        state.closed = true;
        if let Some(cancelled) = &state.active {
            cancelled.store(true, Ordering::Relaxed);
        }
        while state.active.is_some() {
            state = self.lifecycle.completed.wait(state).unwrap();
        }
    }
    pub async fn run(&self, kind: DiagnosticKind) -> Result<DiagnosticReport, SafeError> {
        use std::sync::{
            Arc,
            atomic::{AtomicBool, Ordering},
        };
        struct Cancel(Arc<AtomicBool>);
        impl Drop for Cancel {
            fn drop(&mut self) {
                self.0.store(true, Ordering::Relaxed);
            }
        }
        let cancelled = Cancel(Arc::new(AtomicBool::new(false)));
        let completion = {
            let mut state = self.lifecycle.state.lock().unwrap();
            if state.closed {
                return Err(SafeError::StaleContext);
            }
            // Admission and completion share one lock. A finishing invocation
            // cannot clear the cancellation flag of its successor.
            if state.active.is_some() {
                return Err(SafeError::Busy);
            }
            state.active = Some(cancelled.0.clone());
            DiagnosticCompletion(self.lifecycle.clone())
        };
        let flag = cancelled.0.clone();
        let registration = self.registration.clone();
        tauri::async_runtime::spawn_blocking(move || {
            let _completion = completion;
            execute(&registration, kind, flag)
        })
        .await
        .map_err(|_| SafeError::Storage)?
    }
}
#[cfg(any(windows, target_os = "macos"))]
fn execute(
    registration: &std::path::Path,
    kind: DiagnosticKind,
    cancelled: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Result<DiagnosticReport, SafeError> {
    use sha2::{Digest, Sha256};
    #[cfg(windows)]
    use std::os::windows::fs::OpenOptionsExt;
    use std::{io::Read, time::Duration};
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
        || config.sha256.len() != 64
        || !config.sha256.bytes().all(|v| v.is_ascii_hexdigit())
        || !config.executable.is_absolute()
        || config.executable.file_name().and_then(|v| v.to_str())
            != Some(if cfg!(windows) {
                "powercontext.exe"
            } else {
                "powercontext"
            })
    {
        return Err(SafeError::CompatibilityUnverified);
    }
    // Windows denies replacement through the open handle; macOS executes the verified snapshot.
    let mut options = std::fs::OpenOptions::new();
    options.read(true);
    #[cfg(windows)]
    options.share_mode(1);
    let mut executable = options
        .open(&config.executable)
        .map_err(|_| SafeError::NotFound)?;
    if executable.metadata().map_err(|_| SafeError::Storage)?.len() > 64 * 1024 * 1024 {
        return Err(SafeError::InvalidInput);
    }
    #[cfg(target_os = "macos")]
    let snapshot_directory = tempfile::tempdir().map_err(|_| SafeError::Storage)?;
    #[cfg(target_os = "macos")]
    let snapshot_path = snapshot_directory.path().join("powercontext");
    #[cfg(target_os = "macos")]
    let mut snapshot = std::fs::File::create(&snapshot_path).map_err(|_| SafeError::Storage)?;
    let mut digest = Sha256::new();
    let mut buf = [0; 8192];
    let mut size = 0usize;
    loop {
        let n = executable.read(&mut buf).map_err(|_| SafeError::Storage)?;
        if n == 0 {
            break;
        }
        size += n;
        if size > 64 * 1024 * 1024 {
            return Err(SafeError::InvalidInput);
        }
        digest.update(&buf[..n]);
        #[cfg(target_os = "macos")]
        {
            use std::io::Write;
            snapshot
                .write_all(&buf[..n])
                .map_err(|_| SafeError::Storage)?;
        }
    }
    if format!("{:x}", digest.finalize()) != config.sha256.to_ascii_lowercase() {
        return Err(SafeError::CompatibilityUnverified);
    }
    #[cfg(target_os = "macos")]
    let executable_path = {
        use std::os::unix::fs::PermissionsExt;
        snapshot
            .set_permissions(std::fs::Permissions::from_mode(0o500))
            .map_err(|_| SafeError::Storage)?;
        snapshot_path.as_path()
    };
    #[cfg(windows)]
    let executable_path = config.executable.as_path();
    #[cfg(target_os = "macos")]
    drop(snapshot);
    let timeout = match kind {
        DiagnosticKind::Service => 20,
        DiagnosticKind::Integrations => 60,
    };
    let result = crate::diagnostic_process::run(
        executable_path,
        kind.arguments(),
        Duration::from_secs(timeout),
        cancelled.clone(),
    )?;
    let mut report = project(kind, &result.stdout, result.exit_code)?;
    report.cli_version = crate::diagnostic_process::run(
        executable_path,
        &["--version"],
        Duration::from_secs(15),
        cancelled,
    )
    .ok()
    .and_then(|version| cli_version_from_output(&version.stdout, version.exit_code));
    Ok(report)
}

pub struct DiagnosticHost {
    #[cfg(any(windows, target_os = "macos"))]
    pub local: Option<LocalDiagnostics>,
}
impl DiagnosticHost {
    /// Finish native process cleanup before Tauri directly exits the process.
    pub fn shutdown(&self) {
        #[cfg(any(windows, target_os = "macos"))]
        if let Some(local) = &self.local {
            local.shutdown();
        }
    }
    pub fn new(directory: Option<std::path::PathBuf>) -> Self {
        #[cfg(any(windows, target_os = "macos"))]
        {
            Self {
                local: directory
                    .map(|path| LocalDiagnostics::new(path.join("diagnostic-cli.json"))),
            }
        }
        #[cfg(not(any(windows, target_os = "macos")))]
        {
            let _ = directory;
            Self {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::cli_version_from_output;

    #[test]
    fn cli_version_information_accepts_release_and_development_builds() {
        for version in [
            "1.0.1",
            "1.0.1.dev61+g63f918b7e.d20260919",
            "1.1.1",
            "1.1.1.dev1+g6e237568",
            "1.2.1",
            "1.2.1.dev86+g3d3a6290d",
            "1.2.0",
            "1.3.1",
            "1.3.1.dev2+g338dfa00d",
            "2.0.0",
            "2.0.0rc1",
            "2.0.0.post1",
            "2.0.1.dev3+build_1",
        ] {
            assert_eq!(
                cli_version_from_output(version.as_bytes(), 0),
                Some(version.to_owned())
            );
        }
    }

    #[test]
    fn malformed_or_unavailable_cli_version_information_is_omitted() {
        for version in [
            "",
            "not-a-version",
            "+1.3.1",
            "1.2.1.dev86 with spaces",
            "1.2.1.dev86+未知",
        ] {
            assert_eq!(cli_version_from_output(version.as_bytes(), 0), None);
        }
        assert_eq!(
            cli_version_from_output(format!("1.2.1.dev{}", "a".repeat(128)).as_bytes(), 0),
            None
        );
        assert_eq!(
            cli_version_from_output(b"1.3.1\r\n", 0),
            Some("1.3.1".to_owned())
        );
        assert_eq!(cli_version_from_output(b"1.3.1", 1), None);
        assert_eq!(cli_version_from_output(&[0xff], 0), None);
    }
}
