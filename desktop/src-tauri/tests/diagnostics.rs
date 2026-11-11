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
    diagnostics::{DiagnosticKind, OUTPUT_LIMIT, project},
    error::SafeError,
};
#[test]
fn unhealthy_service_json_survives_exit_one_without_leaking_private_details() {
    let input = serde_json::json!({"support":"supported","registration":"installed","definition":"current",
        "manager_ownership":"owned","manager":"inactive","server_liveness":"unreachable",
        "endpoint":"https://private.test","log_location":"C:/private/account","recovery_action":"private-command", "detail":"secret-token"});
    let result = project(
        DiagnosticKind::Service,
        &serde_json::to_vec(&input).unwrap(),
        1,
    )
    .unwrap();
    assert_eq!(result.exit_code, 1);
    assert_eq!(result.items.len(), 6);
    let output = serde_json::to_string(&result).unwrap();
    assert!(!output.contains("private"));
    assert!(!output.contains("secret-token"));
    assert!(output.contains("unreachable"));
}
#[test]
fn integration_status_is_projected_without_details_or_checks_text() {
    let result = project(DiagnosticKind::Integrations, br#"{"ok":false,"hosts":{"codex":{"status":"failed","ok":false,"checks":{"codex":{"ok":true,"status":"ok","detail":"private-path"},"mcp":{"ok":false,"status":"failed","detail":"secret","checks":{"token":"secret"}}}},"kimi-code":{"status":"failed","ok":false,"checks":{"profile":{"ok":true,"status":"ok","detail":"full"}},"error":"private-path secret-token"}}}"#, 1).unwrap();
    assert_eq!(result.hosts[0].host, "codex");
    assert_eq!(result.hosts[0].checks.len(), 2);
    assert_eq!(result.hosts[1].status, "failed");
    assert_eq!(result.hosts[1].checks[0].field, "profile");
    let output = serde_json::to_string(&result).unwrap();
    assert!(!output.contains("private"));
    assert!(!output.contains("secret"));
}
#[test]
fn malformed_unknown_and_oversized_diagnostics_are_rejected() {
    for output in [
        b"not json".as_slice(),
        br#"{}"#,
        br#"{"ok":true,"hosts":{"codex":{"status":"secret","ok":true,"checks":{}}}}"#,
        br#"{"ok":false,"hosts":{"codex":{"status":"ok","ok":false,"checks":{}}}}"#,
    ] {
        assert_eq!(
            project(DiagnosticKind::Integrations, output, 1).unwrap_err(),
            SafeError::InvalidResponse
        );
    }
    assert_eq!(
        project(DiagnosticKind::Service, &vec![b' '; OUTPUT_LIMIT + 1], 0).unwrap_err(),
        SafeError::ResponseTooLarge
    );
}

#[cfg(any(windows, target_os = "linux"))]
#[tokio::test]
async fn missing_or_mismatched_cli_registration_never_runs_a_program() {
    use powercontext_desktop::diagnostics::LocalDiagnostics;
    let directory = tempfile::tempdir().unwrap();
    let registration = directory.path().join("diagnostic-cli.json");
    let diagnostics = LocalDiagnostics::new(registration.clone());
    assert_eq!(
        diagnostics.run(DiagnosticKind::Service).await.unwrap_err(),
        SafeError::NotFound
    );
    let executable = directory.path().join(if cfg!(windows) {
        "powercontext.exe"
    } else {
        "powercontext"
    });
    std::fs::write(&executable, b"not executable").unwrap();
    let value = serde_json::json!({"executable":executable,"sha256":"0".repeat(64),"source":"explicit_local_installation"});
    std::fs::write(&registration, serde_json::to_vec(&value).unwrap()).unwrap();
    assert_eq!(
        diagnostics.run(DiagnosticKind::Service).await.unwrap_err(),
        SafeError::CompatibilityUnverified
    );
}

#[cfg(unix)]
#[tokio::test]
async fn non_regular_registered_executable_is_rejected_without_blocking_shutdown() {
    use powercontext_desktop::diagnostics::LocalDiagnostics;
    use std::{ffi::CString, os::unix::ffi::OsStrExt, time::Duration};

    let directory = tempfile::tempdir().unwrap();
    let executable = directory.path().join("powercontext");
    let executable_name = CString::new(executable.as_os_str().as_bytes()).unwrap();
    assert_eq!(unsafe { libc::mkfifo(executable_name.as_ptr(), 0o600) }, 0);

    let registration = directory.path().join("diagnostic-cli.json");
    std::fs::write(
        &registration,
        serde_json::to_vec(&serde_json::json!({
            "executable": executable,
            "sha256": "0".repeat(64),
            "source": "explicit_local_installation"
        }))
        .unwrap(),
    )
    .unwrap();

    let diagnostics = LocalDiagnostics::new(registration);
    let result = tokio::time::timeout(
        Duration::from_secs(1),
        diagnostics.run(DiagnosticKind::Service),
    )
    .await
    .expect("FIFO registration must not block executable verification");
    assert_eq!(result.unwrap_err(), SafeError::CompatibilityUnverified);
    diagnostics.shutdown();
}

#[cfg(unix)]
#[tokio::test]
async fn non_regular_registration_is_rejected_without_blocking_shutdown() {
    use powercontext_desktop::diagnostics::LocalDiagnostics;
    use std::{ffi::CString, os::unix::ffi::OsStrExt, time::Duration};

    let directory = tempfile::tempdir().unwrap();
    let registration = directory.path().join("diagnostic-cli.json");
    let registration_name = CString::new(registration.as_os_str().as_bytes()).unwrap();
    assert_eq!(
        unsafe { libc::mkfifo(registration_name.as_ptr(), 0o600) },
        0
    );

    let diagnostics = std::sync::Arc::new(LocalDiagnostics::new(registration));
    let caller = diagnostics.clone();
    let run = tokio::spawn(async move { caller.run(DiagnosticKind::Service).await });
    let result = tokio::time::timeout(Duration::from_secs(1), run)
        .await
        .expect("FIFO registration must not block the diagnostic call")
        .unwrap();
    assert_eq!(result.unwrap_err(), SafeError::CompatibilityUnverified);

    let shutdown = diagnostics.clone();
    tokio::time::timeout(
        Duration::from_secs(1),
        tokio::task::spawn_blocking(move || shutdown.shutdown()),
    )
    .await
    .expect("shutdown must not wait on a FIFO registration")
    .unwrap();
}

#[test]
fn memory_protocol_checks_survive_projection_without_private_details() {
    for status in ["ok", "skipped", "degraded", "failed"] {
        let ok = status == "ok";
        let host_ok = ok || status == "skipped";
        let input = serde_json::json!({
            "ok": host_ok,
            "hosts": {"pi": {
                "ok": host_ok, "status": if host_ok { "ok" } else { status },
                "checks": {
                    "server_memory_protocol": {"ok": ok, "status": status, "detail": "private-server"},
                    "plugin_memory_protocol": {"ok": ok, "status": status,
                        "detail": "private-package", "checks": {"memory_protocol": "secret-token"}}
                }
            }}
        });
        let report = project(
            DiagnosticKind::Integrations,
            &serde_json::to_vec(&input).unwrap(),
            i32::from(!host_ok),
        )
        .unwrap();
        assert_eq!(report.hosts.len(), 1);
        assert_eq!(report.hosts[0].checks.len(), 2);
        for check in &report.hosts[0].checks {
            assert_eq!(check.status, status);
        }
        let output = serde_json::to_string(&report).unwrap();
        assert!(!output.contains("private"));
        assert!(!output.contains("secret-token"));
    }
}
