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

use powercontext_desktop::ipc::allowed_navigation;
use tauri::{
    ipc::{CallbackFn, InvokeBody},
    test::{INVOKE_KEY, get_ipc_response, mock_builder},
    webview::InvokeRequest,
};

// Mock IPC origins follow Tauri's runtime, independently of packaged navigation.
const LOCAL_ORIGIN: &str = if cfg!(any(windows, target_os = "android")) {
    "http://tauri.localhost"
} else {
    "tauri://localhost"
};

fn test_context<R: tauri::Runtime>() -> tauri::Context<R> {
    // The macOS plist must be embedded once per test executable.
    tauri::generate_context!()
}

#[test]
fn permissions_reject_untrusted_window_origin_and_arbitrary_commands() {
    let dir = tempfile::tempdir().unwrap();
    let repository = powercontext_desktop::connections::profiles::ProfileRepository::open(
        dir.path().join("profiles.json"),
        std::sync::Arc::new(powercontext_desktop::credentials::NativeVault),
    )
    .unwrap();
    let app = mock_builder()
        .manage(powercontext_desktop::commands::HostState {
            manager: Ok(
                powercontext_desktop::connections::session::ConnectionManager::new(repository),
            ),
        })
        .invoke_handler(tauri::generate_handler![
            powercontext_desktop::ipc::foundation_info,
            powercontext_desktop::commands::desktop_state,
            powercontext_desktop::commands::disconnect
        ])
        .build(test_context())
        .unwrap();
    for (label, origin, command, allowed) in [
        ("main", LOCAL_ORIGIN, "foundation_info", true),
        ("other", LOCAL_ORIGIN, "foundation_info", false),
        ("main", "https://evil.example", "foundation_info", false),
        ("main", LOCAL_ORIGIN, "desktop_state", true),
        ("other", LOCAL_ORIGIN, "desktop_state", false),
        ("main", "https://evil.example", "desktop_state", false),
        ("main", LOCAL_ORIGIN, "disconnect", true),
        ("other", LOCAL_ORIGIN, "disconnect", false),
        ("main", "https://evil.example", "disconnect", false),
        ("main", LOCAL_ORIGIN, "plugin:shell|execute", false),
        ("main", LOCAL_ORIGIN, "fetch", false),
        ("main", LOCAL_ORIGIN, "credential_read", false),
    ] {
        use tauri::Manager;
        let window = app.get_webview_window(label).unwrap_or_else(|| {
            tauri::WebviewWindowBuilder::new(&app, label, Default::default())
                .build()
                .unwrap()
        });
        let result = get_ipc_response(
            &window,
            InvokeRequest {
                cmd: command.into(),
                callback: CallbackFn(0),
                error: CallbackFn(1),
                url: origin.parse().unwrap(),
                body: InvokeBody::default(),
                headers: Default::default(),
                invoke_key: INVOKE_KEY.into(),
            },
        );
        assert_eq!(
            result.is_ok(),
            allowed,
            "{label} {origin} {command}: {result:?}"
        );
    }
}
#[test]
fn blocks_remote_navigation_and_userinfo() {
    for url in [
        "https://evil.example",
        "file:///C:/secret",
        "javascript:alert(1)",
        "http://tauri.localhost.evil",
        "http://tauri.localhost:8000",
        "http://user@tauri.localhost",
        "tauri://user@localhost",
        "tauri://localhost:8000",
        "tauri://localhost.evil",
        "https://tauri.localhost",
        "tauri://remote-host",
        "tauri://localhost:1234",
        "tauri://remote",
    ] {
        assert!(!allowed_navigation(&url.parse().unwrap()), "{url}");
    }
    assert!(allowed_navigation(&LOCAL_ORIGIN.parse().unwrap()));
    assert!(allowed_navigation(
        &format!("{LOCAL_ORIGIN}/index.html").parse().unwrap()
    ));
    let other_platform = if cfg!(any(windows, target_os = "android")) {
        "tauri://localhost"
    } else {
        "http://tauri.localhost"
    };
    assert!(!allowed_navigation(&other_platform.parse().unwrap()));
}

#[test]
fn diagnostic_command_is_narrow_and_requires_the_trusted_window() {
    let app = mock_builder()
        .manage(powercontext_desktop::diagnostics::DiagnosticHost::new(None))
        .invoke_handler(tauri::generate_handler![
            powercontext_desktop::commands::local_diagnostics
        ])
        .build(test_context())
        .unwrap();
    for (label, origin, kind, expected_missing) in [
        ("main", LOCAL_ORIGIN, "service", true),
        ("main", LOCAL_ORIGIN, "integrations", true),
        ("other", LOCAL_ORIGIN, "service", false),
        ("main", "https://evil.example", "service", false),
        ("main", LOCAL_ORIGIN, "shell", false),
    ] {
        use tauri::Manager;
        let window = app.get_webview_window(label).unwrap_or_else(|| {
            tauri::WebviewWindowBuilder::new(&app, label, Default::default())
                .build()
                .unwrap()
        });
        let result = get_ipc_response(
            &window,
            InvokeRequest {
                cmd: "local_diagnostics".into(),
                callback: CallbackFn(0),
                error: CallbackFn(1),
                url: origin.parse().unwrap(),
                body: InvokeBody::Json(serde_json::json!({"kind":kind})),
                headers: Default::default(),
                invoke_key: INVOKE_KEY.into(),
            },
        );
        let error = result.expect_err("missing CLI or rejected caller");
        assert_eq!(
            error == serde_json::json!("not_found"),
            expected_missing,
            "{label} {origin} {kind}: {error}"
        );
    }
}

#[test]
fn memory_commands_reject_untrusted_callers_before_accessing_the_connection() {
    use powercontext_desktop::{commands, error::SafeError};
    use tauri::Manager;
    let app = mock_builder()
        .manage(commands::HostState {
            manager: Err(SafeError::NotConnected),
        })
        .invoke_handler(tauri::generate_handler![
            commands::remember_memory,
            commands::search_memory,
            commands::atomic_memory_entry,
            commands::cancel_memory_reads
        ])
        .build(test_context())
        .unwrap();
    let args = serde_json::json!({
        "generation": 0,
        "text": "synthetic permission test",
        "query": "synthetic",
        "artifact": {"family":"atomic-memory", "artifact_id":"test", "revision":1}
    });
    for command in [
        "remember_memory",
        "search_memory",
        "atomic_memory_entry",
        "cancel_memory_reads",
    ] {
        for (label, origin) in [
            ("main", LOCAL_ORIGIN),
            ("other", LOCAL_ORIGIN),
            ("main", "https://evil.example"),
        ] {
            let window = app.get_webview_window(label).unwrap_or_else(|| {
                tauri::WebviewWindowBuilder::new(&app, label, Default::default())
                    .build()
                    .unwrap()
            });
            let error = get_ipc_response(
                &window,
                InvokeRequest {
                    cmd: command.into(),
                    callback: CallbackFn(0),
                    error: CallbackFn(1),
                    url: origin.parse().unwrap(),
                    body: InvokeBody::Json(args.clone()),
                    headers: Default::default(),
                    invoke_key: INVOKE_KEY.into(),
                },
            )
            .expect_err("no connection or forbidden caller");
            assert_eq!(
                error.get("code").and_then(serde_json::Value::as_str) == Some("not_connected"),
                label == "main" && origin == LOCAL_ORIGIN,
                "{command} {label} {origin}: {error}"
            );
        }
    }
}

#[test]
fn session_commands_work_without_a_connection_and_reject_untrusted_callers() {
    use powercontext_desktop::{commands, session_observations::SessionObservations};
    use tauri::Manager;
    let app = mock_builder()
        .manage(SessionObservations::default())
        .invoke_handler(tauri::generate_handler![
            commands::session_summary,
            commands::reset_session_observations,
            commands::preview_session_diagnostics
        ])
        .build(test_context())
        .unwrap();
    for command in [
        "session_summary",
        "reset_session_observations",
        "preview_session_diagnostics",
    ] {
        for (label, origin, allowed) in [
            ("main", LOCAL_ORIGIN, true),
            ("other", LOCAL_ORIGIN, false),
            ("main", "https://evil.example", false),
        ] {
            let window = app.get_webview_window(label).unwrap_or_else(|| {
                tauri::WebviewWindowBuilder::new(&app, label, Default::default())
                    .build()
                    .unwrap()
            });
            let result = get_ipc_response(
                &window,
                InvokeRequest {
                    cmd: command.into(),
                    callback: CallbackFn(0),
                    error: CallbackFn(1),
                    url: origin.parse().unwrap(),
                    body: InvokeBody::default(),
                    headers: Default::default(),
                    invoke_key: INVOKE_KEY.into(),
                },
            );
            assert_eq!(
                result.is_ok(),
                allowed,
                "{command} {label} {origin}: {result:?}"
            );
        }
    }
}

#[test]
fn ops_commands_require_the_packaged_main_window_and_reject_arbitrary_input() {
    use powercontext_desktop::{commands, ops_bridge::OpsHost};
    use serde_json::json;
    use tauri::Manager;
    let app = mock_builder()
        .manage(OpsHost::new(None, None, None))
        .invoke_handler(tauri::generate_handler![
            commands::ops_inspect,
            commands::ops_config_validate,
            commands::ops_preflight,
            commands::ops_apply,
            commands::ops_resume,
            commands::ops_operation_status,
            commands::ops_cancel,
            commands::ops_references
        ])
        .build(test_context())
        .unwrap();
    let invoke = |label: &str, origin: &str, command: &str, body| {
        let window = app.get_webview_window(label).unwrap_or_else(|| {
            tauri::WebviewWindowBuilder::new(&app, label, Default::default())
                .build()
                .unwrap()
        });
        get_ipc_response(
            &window,
            InvokeRequest {
                cmd: command.into(),
                callback: CallbackFn(0),
                error: CallbackFn(1),
                url: origin.parse().unwrap(),
                body: InvokeBody::Json(body),
                headers: Default::default(),
                invoke_key: INVOKE_KEY.into(),
            },
        )
    };
    let missing_host = if cfg!(any(windows, target_os = "macos")) {
        "unavailable"
    } else {
        "unsupported_platform"
    };
    for (command, body, expected_error) in [
        ("ops_inspect", json!({}), None),
        ("ops_config_validate", json!({}), None),
        ("ops_references", json!({}), None),
        (
            "ops_preflight",
            json!({"input":{"operation":"start","options":{"restart":true}}}),
            Some("invalid_input"),
        ),
        (
            "ops_apply",
            json!({"input":{"approvalId":"invalid","planId":"a".repeat(64)}}),
            Some("invalid_input"),
        ),
        (
            "ops_resume",
            json!({"input":{"approvalId":"invalid","planId":"a".repeat(64)}}),
            Some("invalid_input"),
        ),
        (
            "ops_operation_status",
            json!({"input":{"operationId":"operation"}}),
            Some(missing_host),
        ),
        (
            "ops_cancel",
            json!({"input":{"operationId":"operation","attempt":0}}),
            Some("invalid_input"),
        ),
    ] {
        let permitted = invoke("main", LOCAL_ORIGIN, command, body.clone());
        if let Some(expected) = expected_error {
            assert_eq!(permitted.unwrap_err(), json!(expected), "{command}");
        } else {
            assert!(permitted.is_ok(), "{command}: {permitted:?}");
        }
        for (label, origin) in [("other", LOCAL_ORIGIN), ("main", "https://evil.example")] {
            let error = invoke(label, origin, command, body.clone()).unwrap_err();
            assert_ne!(error, json!(expected_error), "{command} {label} {origin}");
        }
    }
    for (input, expected) in [
        (
            json!({"operation":"stop","options":{},"executable":"/bin/sh"}),
            "unknown field `executable`",
        ),
        (
            json!({"operation":"shell","options":{}}),
            "unknown variant `shell`",
        ),
    ] {
        let error = invoke(
            "main",
            LOCAL_ORIGIN,
            "ops_preflight",
            json!({"input":input}),
        )
        .unwrap_err();
        assert!(error.as_str().unwrap().contains(expected), "{error}");
    }
}
