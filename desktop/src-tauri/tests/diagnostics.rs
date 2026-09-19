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
    let result = project(DiagnosticKind::Integrations, br#"{"ok":false,"status":"failed","hosts":{"codex":{"presence":"present","codex":{"ok":true,"status":"ok","detail":"private-path"},"mcp":{"ok":false,"status":"failed","detail":"secret","checks":{"token":"secret"}}}}}"#, 1).unwrap();
    assert_eq!(result.hosts[0].host, "codex");
    assert_eq!(result.hosts[0].checks.len(), 2);
    let output = serde_json::to_string(&result).unwrap();
    assert!(!output.contains("private"));
    assert!(!output.contains("secret"));
}
#[test]
fn malformed_unknown_and_oversized_diagnostics_are_rejected() {
    for output in [
        b"not json".as_slice(),
        br#"{}"#,
        br#"{"ok":true,"status":"secret","hosts":{}}"#,
        br#"{"ok":false,"status":"ok","hosts":{}}"#,
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

#[cfg(windows)]
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
    let executable = directory.path().join("powercontext.exe");
    std::fs::write(&executable, b"not executable").unwrap();
    let value = serde_json::json!({"executable":executable,"sha256":"0".repeat(64),"version":"1.0.1.dev61+g63f918b7e.d20260919","source":"explicit_local_installation"});
    std::fs::write(&registration, serde_json::to_vec(&value).unwrap()).unwrap();
    assert_eq!(
        diagnostics.run(DiagnosticKind::Service).await.unwrap_err(),
        SafeError::CompatibilityUnverified
    );
}
