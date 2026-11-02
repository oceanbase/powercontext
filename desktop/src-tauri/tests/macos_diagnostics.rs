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

#![cfg(target_os = "macos")]

use powercontext_desktop::{
    diagnostics::{DiagnosticKind, LocalDiagnostics},
    error::SafeError,
};
use sha2::{Digest, Sha256};

#[tokio::test]
async fn registered_launcher_is_verified_and_projected_without_private_output() {
    let directory = tempfile::tempdir().unwrap();
    let executable = directory.path().join("powercontext");
    // A real subprocess with the public CLI protocol. It needs no Server or user data.
    std::fs::write(&executable, r##"#!/bin/sh
if [ "$1" = "--version" ]; then
    printf '1.3.0\n'
else
    printf '%s\n' '{"support":"supported","registration":"not_installed","definition":"unknown","manager_ownership":"unknown","manager":"unknown","server_liveness":"unknown","private":"must-not-reach-IPC"}'
    exit 1
fi
"##).unwrap();
    let registration = directory.path().join("diagnostic-cli.json");
    let mut config = serde_json::json!({
        "executable": executable,
        "sha256": format!("{:x}", Sha256::digest(std::fs::read(&executable).unwrap())),
        "source": "explicit_local_installation"
    });
    std::fs::write(&registration, serde_json::to_vec(&config).unwrap()).unwrap();
    let report = LocalDiagnostics::new(registration.clone())
        .run(DiagnosticKind::Service)
        .await
        .unwrap();
    assert_eq!(report.exit_code, 1);
    assert_eq!(report.cli_version.as_deref(), Some("1.3.0"));
    assert!(
        report
            .items
            .iter()
            .any(|item| item.field == "registration" && item.status == "not_installed")
    );
    assert!(
        !serde_json::to_string(&report)
            .unwrap()
            .contains("must-not-reach-IPC")
    );
    config["sha256"] = "0".repeat(64).into();
    std::fs::write(&registration, serde_json::to_vec(&config).unwrap()).unwrap();
    assert_eq!(
        LocalDiagnostics::new(registration)
            .run(DiagnosticKind::Service)
            .await
            .unwrap_err(),
        SafeError::CompatibilityUnverified
    );
}

#[tokio::test]
async fn shutdown_terminates_inflight_launcher_and_descendants_and_closes_admission() {
    use std::{sync::Arc, time::Duration};
    let directory = tempfile::tempdir().unwrap();
    let executable = directory.path().join("powercontext");
    let pid_file = directory.path().join("pids");
    std::fs::write(
        &executable,
        format!(
            "#!/bin/sh\nsleep 300 &\necho $$ $! > '{}'\nwait\n",
            pid_file.display()
        ),
    )
    .unwrap();
    let registration = directory.path().join("diagnostic-cli.json");
    std::fs::write(
        &registration,
        serde_json::to_vec(&serde_json::json!({
            "executable": executable,
            "sha256": format!("{:x}", Sha256::digest(std::fs::read(&executable).unwrap())),
            "source": "explicit_local_installation"
        }))
        .unwrap(),
    )
    .unwrap();
    let local = Arc::new(LocalDiagnostics::new(registration));
    let worker = local.clone();
    let mut check = tokio::spawn(async move { worker.run(DiagnosticKind::Service).await });
    let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    let pids = loop {
        if let Ok(text) = std::fs::read_to_string(&pid_file) {
            let pids: Vec<i32> = text
                .split_whitespace()
                .filter_map(|pid| pid.parse().ok())
                .collect();
            if pids.len() == 2 {
                break pids;
            }
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "launcher did not become ready"
        );
        tokio::time::sleep(Duration::from_millis(10)).await;
    };
    struct Cleanup(i32);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            unsafe {
                libc::kill(-self.0, libc::SIGKILL);
            }
        }
    }
    let _cleanup = Cleanup(pids[0]);
    let shutdown = local.clone();
    let started = std::time::Instant::now();
    tokio::task::spawn_blocking(move || shutdown.shutdown())
        .await
        .unwrap();
    // Inspect before aborting the async caller: dropping that future also cancels
    // diagnostics, which would hide a missing shutdown implementation.
    let stopped: Vec<bool> = pids
        .iter()
        .map(|pid| {
            let state = std::process::Command::new("/bin/ps")
                .args(["-o", "stat=", "-p", &pid.to_string()])
                .output()
                .unwrap();
            let state = String::from_utf8_lossy(&state.stdout);
            state.trim().is_empty() || state.trim().starts_with('Z')
        })
        .collect();
    let result = tokio::time::timeout(Duration::from_secs(2), &mut check).await;
    check.abort();
    assert!(
        stopped.iter().all(|stopped| *stopped),
        "shutdown left diagnostic processes running"
    );
    assert!(matches!(result, Ok(Ok(Err(SafeError::StaleContext)))));
    assert!(started.elapsed() < Duration::from_secs(3));
    assert_eq!(
        local.run(DiagnosticKind::Service).await.unwrap_err(),
        SafeError::StaleContext
    );
    local.shutdown(); // Repeated shutdown remains safe.
}

#[tokio::test]
async fn shutdown_cancels_the_next_check_after_a_completed_check() {
    use std::{sync::Arc, time::Duration};
    let directory = tempfile::tempdir().unwrap();
    let executable = directory.path().join("powercontext");
    let first = directory.path().join("first");
    let pid_file = directory.path().join("pid");
    std::fs::write(&executable, format!(r##"#!/bin/sh
if [ "$1" = "--version" ]; then
    echo 1.4.0
elif [ -f '{first}' ]; then
    echo $$ > '{pid}'
    sleep 300 &
    wait
else
    touch '{first}'
    printf '%s\n' '{{"support":"supported","registration":"not_installed","definition":"unknown","manager_ownership":"unknown","manager":"unknown","server_liveness":"unknown"}}'
fi
"##, first = first.display(), pid = pid_file.display())).unwrap();
    let registration = directory.path().join("diagnostic-cli.json");
    std::fs::write(
        &registration,
        serde_json::to_vec(&serde_json::json!({
            "executable": executable,
            "sha256": format!("{:x}", Sha256::digest(std::fs::read(&executable).unwrap())),
            "source": "explicit_local_installation"
        }))
        .unwrap(),
    )
    .unwrap();
    let local = Arc::new(LocalDiagnostics::new(registration));
    let worker = local.clone();
    let first_check = tokio::spawn(async move { worker.run(DiagnosticKind::Service).await });
    let ready = tokio::time::Instant::now() + Duration::from_secs(10);
    while !first.exists() {
        assert!(
            tokio::time::Instant::now() < ready,
            "first check did not start"
        );
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    let worker = local.clone();
    let second_check = tokio::spawn(async move {
        loop {
            assert!(
                tokio::time::Instant::now() < ready,
                "diagnostic admission did not recover"
            );
            match worker.run(DiagnosticKind::Service).await {
                Err(SafeError::Busy) => tokio::task::yield_now().await,
                result => break result,
            }
        }
    });
    tokio::time::timeout(Duration::from_secs(10), first_check)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    let pid = loop {
        if let Ok(text) = std::fs::read_to_string(&pid_file)
            && let Ok(pid) = text.trim().parse::<i32>()
        {
            break pid;
        }
        assert!(
            tokio::time::Instant::now() < ready,
            "second check did not start"
        );
        tokio::time::sleep(Duration::from_millis(10)).await;
    };
    struct Cleanup(i32);
    impl Drop for Cleanup {
        fn drop(&mut self) {
            unsafe {
                libc::kill(-self.0, libc::SIGKILL);
            }
        }
    }
    let _cleanup = Cleanup(pid);
    let shutdown = local.clone();
    tokio::task::spawn_blocking(move || shutdown.shutdown())
        .await
        .unwrap();
    // Keep the caller alive: aborting it would hide a lost shutdown cancellation.
    let state = std::process::Command::new("/bin/ps")
        .args(["-o", "stat=", "-p", &pid.to_string()])
        .output()
        .unwrap();
    let state = String::from_utf8_lossy(&state.stdout);
    assert!(
        state.trim().is_empty() || state.trim().starts_with('Z'),
        "shutdown lost the next check's process group"
    );
    assert_eq!(
        tokio::time::timeout(Duration::from_secs(2), second_check)
            .await
            .unwrap()
            .unwrap()
            .unwrap_err(),
        SafeError::StaleContext
    );
    assert_eq!(
        local.run(DiagnosticKind::Service).await.unwrap_err(),
        SafeError::StaleContext
    );
}
