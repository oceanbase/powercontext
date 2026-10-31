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

pub mod commands;
pub mod connections;
pub mod credentials;
#[cfg(windows)]
mod diagnostic_process;
#[cfg(target_os = "macos")]
#[path = "diagnostic_process_macos.rs"]
mod diagnostic_process;
pub mod diagnostics;
pub mod error;
pub mod ipc;
pub mod ops_bridge;
pub mod session_observations;
pub mod transport;

use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // A second launch only activates the existing editor; never replaces its context.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .invoke_handler(tauri::generate_handler![
            ipc::foundation_info,
            commands::local_diagnostics,
            commands::ops_references,
            commands::ops_inspect,
            commands::ops_config_validate,
            commands::ops_preflight,
            commands::ops_apply,
            commands::ops_operation_status,
            commands::ops_cancel,
            commands::ops_resume,
            commands::remember_memory,
            commands::search_memory,
            commands::atomic_memory_entry,
            commands::cancel_memory_reads,
            commands::desktop_state,
            commands::save_profile,
            commands::remove_profile,
            commands::check_connection,
            commands::disconnect,
            commands::invalidate_profile,
            commands::list_scopes,
            commands::cancel_scope_reads,
            commands::default_scope,
            commands::select_scope,
            commands::session_summary,
            commands::reset_session_observations,
            commands::preview_session_diagnostics
        ])
        .setup(|app| {
            let manager = app
                .path()
                .app_data_dir()
                .map_err(|_| error::SafeError::Storage)
                .and_then(|dir| {
                    connections::profiles::ProfileRepository::open(
                        dir.join("profiles.json"),
                        std::sync::Arc::new(credentials::NativeVault),
                    )
                })
                .map(connections::session::ConnectionManager::new);
            // Fail closed if another process owns this user's channel storage, including
            // the interval before the single-instance plugin's activation window exists.
            if matches!(&manager, Err(error::SafeError::Busy)) {
                return Err(
                    std::io::Error::other("Desktop profile storage is already in use").into(),
                );
            }
            let observations = manager
                .as_ref()
                .map(|manager| manager.observations().clone())
                .unwrap_or_default();
            app.manage(observations);
            app.manage(commands::HostState { manager });
            app.manage(diagnostics::DiagnosticHost::new(
                app.path().app_data_dir().ok(),
            ));
            app.manage(ops_bridge::OpsHost::new(
                app.path().resource_dir().ok(),
                app.path().local_data_dir().ok(),
                app.path().home_dir().ok(),
            ));
            let config = &app.config().app.windows[0];
            tauri::WebviewWindowBuilder::from_config(app, config)?
                .on_navigation(ipc::allowed_navigation)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("desktop host failed")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit)
                && let Some(host) = app.try_state::<diagnostics::DiagnosticHost>()
            {
                // Tauri exits directly after this callback; worker Drop alone
                // cannot supervise child processes once the application exits.
                host.shutdown();
            }
            if matches!(event, tauri::RunEvent::Exit)
                && let Some(host) = app.try_state::<ops_bridge::OpsHost>()
            {
                // Ops owns its durable operation journal and service locks. Closing Desktop
                // stops new IPC admission but never signals a service-manager process.
                host.shutdown();
            }
        });
}
