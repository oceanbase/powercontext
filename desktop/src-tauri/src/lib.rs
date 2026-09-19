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
pub mod diagnostics;
pub mod error;
pub mod ipc;
pub mod transport;

use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            ipc::foundation_info,
            commands::local_diagnostics,
            commands::remember_memory,
            commands::search_memory,
            commands::memory_entry,
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
            commands::select_scope
        ])
        .setup(|app| {
            let manager = app
                .path()
                .app_data_dir()
                .map_err(|_| error::SafeError::Storage)
                .and_then(|dir| {
                    connections::profiles::ProfileRepository::open(
                        dir.join("profiles.json"),
                        std::sync::Arc::new(credentials::WindowsVault),
                    )
                })
                .map(connections::session::ConnectionManager::new);
            app.manage(commands::HostState { manager });
            app.manage(diagnostics::DiagnosticHost::new(
                app.path().app_data_dir().ok(),
            ));
            let config = &app.config().app.windows[0];
            tauri::WebviewWindowBuilder::from_config(app, config)?
                .on_navigation(ipc::allowed_navigation)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("desktop host failed");
}
