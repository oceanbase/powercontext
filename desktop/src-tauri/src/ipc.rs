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

use crate::error::SafeError;
use serde::Serialize;
use ts_rs::TS;

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct FoundationInfo {
    pub version: String,
    pub phase: String,
    pub credential_backend: String,
    pub server_connected: bool,
}

pub fn authorize_window(label: &str) -> Result<(), SafeError> {
    if label == "main" {
        Ok(())
    } else {
        Err(SafeError::UnauthorizedWindow)
    }
}

#[tauri::command]
pub fn foundation_info<R: tauri::Runtime>(
    window: tauri::WebviewWindow<R>,
) -> Result<FoundationInfo, SafeError> {
    authorize_window(window.label())?;
    Ok(FoundationInfo {
        version: env!("CARGO_PKG_VERSION").into(),
        phase: "S3".into(),
        credential_backend: if cfg!(windows) {
            "windows_credential_manager"
        } else {
            "unavailable"
        }
        .into(),
        server_connected: false,
    })
}

pub fn allowed_navigation(url: &url::Url) -> bool {
    let packaged =
        url.scheme() == "http" && url.host_str() == Some("tauri.localhost") && url.port().is_none();
    let development =
        cfg!(debug_assertions) && url.origin().ascii_serialization() == "http://127.0.0.1:1420";
    (packaged || development) && url.username().is_empty() && url.password().is_none()
}
