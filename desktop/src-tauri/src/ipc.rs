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
        credential_backend: credential_backend(current_target_os()).into(),
        server_connected: false,
    })
}

fn credential_backend(target_os: &str) -> &'static str {
    match target_os {
        "windows" => "windows_credential_manager",
        "macos" => "macos_keychain",
        "linux" => "linux_secret_service",
        _ => "unavailable",
    }
}

fn packaged_origin(target_os: &str) -> Option<(&'static str, &'static str)> {
    match target_os {
        "windows" | "android" => Some(("http", "tauri.localhost")),
        "linux" | "macos" | "ios" => Some(("tauri", "localhost")),
        _ => None,
    }
}

fn current_target_os() -> &'static str {
    if cfg!(windows) {
        "windows"
    } else if cfg!(target_os = "android") {
        "android"
    } else if cfg!(target_os = "linux") {
        "linux"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(target_os = "ios") {
        "ios"
    } else {
        "other"
    }
}

fn packaged_navigation_allowed(url: &url::Url, target_os: &str) -> bool {
    packaged_origin(target_os).is_some_and(|(scheme, host)| {
        url.scheme() == scheme && url.host_str() == Some(host) && url.port().is_none()
    })
}

pub fn allowed_navigation(url: &url::Url) -> bool {
    let packaged = packaged_navigation_allowed(url, current_target_os());
    let development =
        cfg!(debug_assertions) && url.origin().ascii_serialization() == "http://127.0.0.1:1420";
    (packaged || development) && url.username().is_empty() && url.password().is_none()
}

#[cfg(test)]
mod tests {
    use super::{credential_backend, packaged_navigation_allowed};

    #[test]
    fn credential_backend_is_explicit_for_supported_targets() {
        assert_eq!(credential_backend("windows"), "windows_credential_manager");
        assert_eq!(credential_backend("macos"), "macos_keychain");
        assert_eq!(credential_backend("linux"), "linux_secret_service");
        assert_eq!(credential_backend("other"), "unavailable");
    }

    #[test]
    fn packaged_origins_are_explicit_for_each_supported_target() {
        for (target_os, accepted, rejected) in [
            ("windows", "http://tauri.localhost/", "tauri://localhost/"),
            ("android", "http://tauri.localhost/", "tauri://localhost/"),
            ("linux", "tauri://localhost/", "http://tauri.localhost/"),
            ("macos", "tauri://localhost/", "http://tauri.localhost/"),
            ("ios", "tauri://localhost/", "http://tauri.localhost/"),
        ] {
            assert!(
                packaged_navigation_allowed(&accepted.parse().unwrap(), target_os),
                "{target_os} should accept {accepted}"
            );
            assert!(
                !packaged_navigation_allowed(&rejected.parse().unwrap(), target_os),
                "{target_os} should reject {rejected}"
            );
        }
    }
}
