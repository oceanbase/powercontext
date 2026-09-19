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
    transport::{
        ApiFailure,
        wire::{ScopeDescriptor, ScopePage},
    },
};
pub struct HostState {
    pub manager: Result<ConnectionManager, SafeError>,
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
    #[cfg(windows)]
    {
        state
            .local
            .as_ref()
            .ok_or(SafeError::NotFound)?
            .run(kind)
            .await
    }
    #[cfg(not(windows))]
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
) -> Result<crate::transport::wire::SearchMemoryResponse, ApiFailure> {
    manager(&window, &state)?
        .search_memory(generation, &query)
        .await
}
#[tauri::command]
pub async fn memory_entry<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
    state: tauri::State<'_, HostState>,
    generation: u32,
    citation: crate::transport::wire::MemoryCitation,
) -> Result<crate::transport::wire::MemoryEntry, ApiFailure> {
    manager(&window, &state)?
        .memory_entry(generation, &citation)
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
