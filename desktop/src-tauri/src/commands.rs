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

use crate::{
    connections::{
        profiles::ProfileInput,
        session::{ConnectionManager, DesktopState},
    },
    error::SafeError,
    ipc::authorize_window,
    session_observations::{SessionObservations, SessionSummary},
    transport::{
        ApiFailure,
        wire::{ScopeDescriptor, ScopePage},
    },
};
pub struct HostState {
    pub manager: Result<ConnectionManager, SafeError>,
}

#[tauri::command]
pub fn session_summary<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, SessionObservations>,
) -> Result<SessionSummary, SafeError> {
    authorize_window(window.label())?;
    state.summary()
}

#[tauri::command]
pub fn reset_session_observations<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, SessionObservations>,
) -> Result<(), SafeError> {
    authorize_window(window.label())?;
    state.reset()
}

#[tauri::command]
pub fn preview_session_diagnostics<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, SessionObservations>,
) -> Result<String, SafeError> {
    authorize_window(window.label())?;
    state.diagnostic_text()
}
fn manager<'a, R: tauri::Runtime>(
    window: &tauri::WebviewWindow<R>,
    state: &'a HostState,
) -> Result<&'a ConnectionManager, ApiFailure> {
    authorize_window(window.label())?;
    state.manager.as_ref().map_err(|e| ApiFailure::before(*e))
}

#[tauri::command]
pub fn desktop_state<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
) -> Result<DesktopState, ApiFailure> {
    manager(&window, &state)?.state().map_err(Into::into)
}

#[tauri::command]
pub fn save_profile<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    input: ProfileInput,
) -> Result<DesktopState, ApiFailure> {
    manager(&window, &state)?
        .save_profile(input)
        .map_err(Into::into)
}

#[tauri::command]
pub fn remove_profile<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    id: String,
    revision: u32,
) -> Result<DesktopState, ApiFailure> {
    manager(&window, &state)?
        .remove_profile(&id, revision)
        .map_err(Into::into)
}

#[tauri::command]
pub async fn check_connection<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    id: String,
    activate: bool,
) -> Result<DesktopState, ApiFailure> {
    manager(&window, &state)?.check(&id, activate).await
}

#[tauri::command]
pub fn disconnect<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
) -> Result<DesktopState, ApiFailure> {
    manager(&window, &state)?.disconnect().map_err(Into::into)
}

#[tauri::command]
pub fn invalidate_profile<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    id: String,
) -> Result<DesktopState, ApiFailure> {
    manager(&window, &state)?
        .invalidate_profile(&id)
        .map_err(Into::into)
}

#[tauri::command]
pub async fn list_scopes<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
    query: String,
    cursor: Option<String>,
) -> Result<ScopePage, ApiFailure> {
    manager(&window, &state)?
        .scopes(generation, &query, cursor.as_deref())
        .await
}

#[tauri::command]
pub async fn default_scope<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
) -> Result<ScopeDescriptor, ApiFailure> {
    manager(&window, &state)?.default_scope(generation).await
}

#[tauri::command]
pub async fn select_scope<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
    id: String,
) -> Result<DesktopState, ApiFailure> {
    manager(&window, &state)?
        .select_scope(generation, &id)
        .await
}

#[tauri::command]
pub fn cancel_scope_reads<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
) -> Result<(), ApiFailure> {
    manager(&window, &state)?
        .cancel_scope_reads(generation)
        .map_err(Into::into)
}

#[tauri::command]
pub async fn local_diagnostics<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::diagnostics::DiagnosticHost>,
    kind: crate::diagnostics::DiagnosticKind,
) -> Result<crate::diagnostics::DiagnosticReport, SafeError> {
    authorize_window(window.label())?;
    #[cfg(any(windows, target_os = "macos"))]
    {
        state
            .local
            .as_ref()
            .ok_or(SafeError::NotFound)?
            .run(kind)
            .await
    }
    #[cfg(not(any(windows, target_os = "macos")))]
    {
        let _ = (state, kind);
        Err(SafeError::NotFound)
    }
}

#[tauri::command]
pub async fn remember_memory<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
    text: String,
) -> Result<crate::connections::session::WriteOutcome, ApiFailure> {
    manager(&window, &state)?.remember(generation, &text).await
}
#[tauri::command]
pub async fn search_memory<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
    query: String,
) -> Result<crate::transport::MemorySearchResponse, ApiFailure> {
    manager(&window, &state)?
        .search_memory(generation, &query)
        .await
}
#[tauri::command]
pub async fn atomic_memory_entry<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
    artifact: crate::transport::wire::ArtifactReference,
) -> Result<crate::transport::AtomicMemoryDetail, ApiFailure> {
    manager(&window, &state)?
        .atomic_memory_entry(generation, &artifact)
        .await
}
#[tauri::command]
pub fn cancel_memory_reads<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
) -> Result<(), ApiFailure> {
    manager(&window, &state)?
        .cancel_memory_reads(generation)
        .map_err(Into::into)
}

#[tauri::command]
pub async fn ops_references<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::ops_bridge::OpsHost>,
) -> Result<crate::ops_bridge::OpsReferences, crate::ops_bridge::OpsBridgeError> {
    authorize_window(window.label()).map_err(|_| crate::ops_bridge::OpsBridgeError::Forbidden)?;
    let host = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || host.references())
        .await
        .map_err(|_| crate::ops_bridge::OpsBridgeError::ProcessFailure)?
}
#[tauri::command]
pub async fn ops_inspect<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::ops_bridge::OpsHost>,
) -> Result<crate::ops_bridge::OpsInspectionReply, crate::ops_bridge::OpsBridgeError> {
    authorize_window(window.label()).map_err(|_| crate::ops_bridge::OpsBridgeError::Forbidden)?;
    let host = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || host.inspect())
        .await
        .map_err(|_| crate::ops_bridge::OpsBridgeError::ProcessFailure)
}
#[tauri::command]
pub async fn ops_config_validate<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::ops_bridge::OpsHost>,
) -> Result<crate::ops_bridge::OpsValidationReply, crate::ops_bridge::OpsBridgeError> {
    authorize_window(window.label()).map_err(|_| crate::ops_bridge::OpsBridgeError::Forbidden)?;
    let host = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || host.config_validate())
        .await
        .map_err(|_| crate::ops_bridge::OpsBridgeError::ProcessFailure)
}
#[tauri::command]
pub async fn ops_preflight<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::ops_bridge::OpsHost>,
    input: crate::ops_bridge::OpsPreflightInput,
) -> Result<crate::ops_bridge::OpsPreflightReply, crate::ops_bridge::OpsBridgeError> {
    authorize_window(window.label()).map_err(|_| crate::ops_bridge::OpsBridgeError::Forbidden)?;
    let host = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || host.preflight(input))
        .await
        .map_err(|_| crate::ops_bridge::OpsBridgeError::ProcessFailure)?
}
#[tauri::command]
pub async fn ops_apply<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::ops_bridge::OpsHost>,
    input: crate::ops_bridge::OpsApprovalInput,
) -> Result<crate::ops_bridge::OpsOperationReply, crate::ops_bridge::OpsBridgeError> {
    use tauri::Emitter;
    authorize_window(window.label()).map_err(|_| crate::ops_bridge::OpsBridgeError::Forbidden)?;
    let host = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        host.apply(input, move |event| {
            let _ = window.emit_to(
                tauri::EventTarget::webview_window("main"),
                crate::ops_bridge::PROGRESS_EVENT,
                event,
            );
        })
    })
    .await
    .map_err(|_| crate::ops_bridge::OpsBridgeError::ProcessFailure)?
}
#[tauri::command]
pub async fn ops_resume<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::ops_bridge::OpsHost>,
    input: crate::ops_bridge::OpsApprovalInput,
) -> Result<crate::ops_bridge::OpsOperationReply, crate::ops_bridge::OpsBridgeError> {
    use tauri::Emitter;
    authorize_window(window.label()).map_err(|_| crate::ops_bridge::OpsBridgeError::Forbidden)?;
    let host = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || {
        host.resume(input, move |event| {
            let _ = window.emit_to(
                tauri::EventTarget::webview_window("main"),
                crate::ops_bridge::PROGRESS_EVENT,
                event,
            );
        })
    })
    .await
    .map_err(|_| crate::ops_bridge::OpsBridgeError::ProcessFailure)?
}
#[tauri::command]
pub async fn ops_operation_status<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::ops_bridge::OpsHost>,
    input: crate::ops_bridge::OpsStatusInput,
) -> Result<crate::ops_bridge::OpsOperationReply, crate::ops_bridge::OpsBridgeError> {
    authorize_window(window.label()).map_err(|_| crate::ops_bridge::OpsBridgeError::Forbidden)?;
    let host = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || host.operation_status(input))
        .await
        .map_err(|_| crate::ops_bridge::OpsBridgeError::ProcessFailure)?
}
#[tauri::command]
pub async fn ops_cancel<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, crate::ops_bridge::OpsHost>,
    input: crate::ops_bridge::OpsCancelInput,
) -> Result<crate::ops_bridge::OpsOperationReply, crate::ops_bridge::OpsBridgeError> {
    authorize_window(window.label()).map_err(|_| crate::ops_bridge::OpsBridgeError::Forbidden)?;
    let host = state.inner().clone();
    tauri::async_runtime::spawn_blocking(move || host.cancel(input))
        .await
        .map_err(|_| crate::ops_bridge::OpsBridgeError::ProcessFailure)?
}

/// An explicit setup action may probe readiness and select the public default Scope.
#[tauri::command]
pub async fn connect_local_server<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    ops: tauri::State<'_, crate::ops_bridge::OpsHost>,
    generation: u32,
) -> Result<DesktopState, ApiFailure> {
    let manager = manager(&window, &state)?;
    if manager.state()?.generation != generation {
        return Err(SafeError::StaleContext.into());
    }
    let host = ops.inner().clone();
    let qualifications = manager.state()?.compatibility_profiles;
    let (endpoint, qualification, version) =
        tauri::async_runtime::spawn_blocking(move || host.connection_target(&qualifications))
            .await
            .map_err(|_| SafeError::ProcessFailure)?
            .map_err(|error| match error {
                crate::ops_bridge::OpsBridgeError::IntegrityFailure => SafeError::IntegrityFailure,
                crate::ops_bridge::OpsBridgeError::InvalidInput => SafeError::InvalidInput,
                crate::ops_bridge::OpsBridgeError::Storage => SafeError::Storage,
                crate::ops_bridge::OpsBridgeError::Busy => SafeError::Busy,
                crate::ops_bridge::OpsBridgeError::Timeout => SafeError::Timeout,
                crate::ops_bridge::OpsBridgeError::ForeignBinding => SafeError::Conflict,
                crate::ops_bridge::OpsBridgeError::InvalidResponse => SafeError::InvalidResponse,
                crate::ops_bridge::OpsBridgeError::Unavailable
                | crate::ops_bridge::OpsBridgeError::UnsupportedPlatform => {
                    SafeError::RuntimeNotReady
                }
                _ => SafeError::ProcessFailure,
            })?;
    manager
        .connect_local(generation, endpoint, qualification, version)
        .await
}
