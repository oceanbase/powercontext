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

#[cfg(any(windows, target_os = "linux"))]
#[tokio::main]
async fn main() {
    use powercontext_desktop::{
        diagnostics::{DiagnosticKind, LocalDiagnostics},
        error::SafeError,
    };
    #[cfg(windows)]
    use sha2::{Digest, Sha256};
    #[cfg(windows)]
    {
        // A copied probe is a synthetic CLI whose diagnostic protocol works without --version.
        let probe = std::env::current_exe().unwrap();
        if probe.file_name().and_then(|name| name.to_str()) == Some("powercontext.exe") {
            if std::env::args().skip(1).collect::<Vec<_>>() == ["service", "status", "--json"] {
                println!(
                    "{}",
                    serde_json::json!({"support":"supported","registration":"not_installed",
                    "definition":"unknown","manager_ownership":"unknown","manager":"unknown",
                    "server_liveness":"unknown"})
                );
            } else {
                std::process::exit(1);
            }
            return;
        }
    }
    let path = std::path::PathBuf::from(
        std::env::args_os()
            .nth(1)
            .expect("isolated fixture registration"),
    );
    let registration: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path).expect("read isolated registration"))
            .expect("parse isolated registration");
    let invalid_directory = tempfile::tempdir().expect("isolated invalid registrations");
    assert!(
        registration.get("version").is_none(),
        "registration must not require a version"
    );
    let mut invalid = registration.clone();
    invalid["sha256"] = "0".repeat(64).into();
    let invalid_path = invalid_directory.path().join("digest.json");
    std::fs::write(&invalid_path, serde_json::to_vec(&invalid).unwrap()).unwrap();
    assert_eq!(
        LocalDiagnostics::new(invalid_path)
            .run(DiagnosticKind::Service)
            .await
            .unwrap_err(),
        SafeError::CompatibilityUnverified,
        "mismatched digest must remain rejected"
    );
    let diagnostics = LocalDiagnostics::new(path);
    let service = diagnostics
        .run(DiagnosticKind::Service)
        .await
        .expect("real service status projection");
    let integrations = diagnostics
        .run(DiagnosticKind::Integrations)
        .await
        .expect("real integrations projection");
    #[cfg(windows)]
    {
        assert!(
            service
                .items
                .iter()
                .any(|v| v.field == "registration" && v.status == "not_installed")
        );
        assert_eq!(integrations.hosts.len(), 10);
    }

    #[cfg(target_os = "linux")]
    {
        let support = service
            .items
            .iter()
            .find(|item| item.field == "support")
            .expect("service support field");
        assert!(matches!(
            support.status.as_str(),
            "supported" | "unsupported"
        ));

        let registration = service
            .items
            .iter()
            .find(|item| item.field == "registration")
            .expect("service registration field");
        assert!(matches!(
            registration.status.as_str(),
            "installed" | "not_installed" | "unknown"
        ));

        let mut hosts: Vec<_> = integrations
            .hosts
            .iter()
            .map(|host| host.host.as_str())
            .collect();
        hosts.sort_unstable();
        assert_eq!(
            hosts,
            vec![
                "claude-code",
                "codex",
                "dsh",
                "hermes",
                "kimi-code",
                "openclaw",
                "opencode",
                "pi",
                "workbuddy",
                "zcode",
            ]
        );
    }
    let mut legacy = registration.clone();
    legacy["version"] = "0.0.0".into();
    let legacy_path = invalid_directory.path().join("legacy.json");
    std::fs::write(&legacy_path, serde_json::to_vec(&legacy).unwrap()).unwrap();
    let legacy_service = LocalDiagnostics::new(legacy_path)
        .run(DiagnosticKind::Service)
        .await
        .unwrap();
    assert_eq!(legacy_service.items, service.items);
    assert_eq!(legacy_service.cli_version, service.cli_version);

    #[cfg(windows)]
    {
        let probe = std::env::current_exe().unwrap();
        let synthetic_executable = invalid_directory.path().join("powercontext.exe");
        std::fs::copy(probe, &synthetic_executable).unwrap();
        let synthetic_registration = invalid_directory.path().join("unavailable-version.json");
        std::fs::write(
            &synthetic_registration,
            serde_json::to_vec(&serde_json::json!({
                "executable":synthetic_executable,
                "sha256":format!("{:x}", Sha256::digest(std::fs::read(&synthetic_executable).unwrap())),
                "source":"explicit_local_installation"
            }))
            .unwrap(),
        )
        .unwrap();
        let synthetic_report = LocalDiagnostics::new(synthetic_registration)
            .run(DiagnosticKind::Service)
            .await
            .unwrap();
        assert_eq!(synthetic_report.cli_version, None);
        assert!(
            synthetic_report
                .items
                .iter()
                .any(|item| item.field == "registration" && item.status == "not_installed")
        );
    }
    println!(
        "{}",
        serde_json::json!({"service":service,"integrations":integrations,
            "registrationChecks":{"versionlessRegistrationAccepted":true,"legacyVersionIgnored":true,
                "digestMismatchRejected":true,"unavailableVersionDoesNotBlock":true}})
    );
}
#[cfg(not(any(windows, target_os = "linux")))]
fn main() {
    panic!("Windows or Linux qualification required");
}
