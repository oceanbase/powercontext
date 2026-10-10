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

fn main() {
    if std::env::var_os("CARGO_FEATURE_CI_FIXTURES").is_some() {
        let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../.artifacts/ci-compatibility.json");
        println!("cargo:rerun-if-changed={}", manifest.display());
        let output = std::path::PathBuf::from(std::env::var_os("OUT_DIR").unwrap());
        std::fs::copy(manifest, output.join("ci-compatibility.json")).expect(
            "ci-fixtures requires explicit built-wheel qualification; run fixture_qualification.py",
        );
    }
    // Integration-test executables also need ComCtl32 v6 for Tauri's menu imports.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows") {
        println!("cargo:rustc-link-arg-tests=/MANIFEST:EMBED");
        println!(
            "cargo:rustc-link-arg-tests=/MANIFESTDEPENDENCY:type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'"
        );
    }
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "foundation_info",
            "local_diagnostics",
            "remember_memory",
            "search_memory",
            "memory_entry",
            "cancel_memory_reads",
            "desktop_state",
            "save_profile",
            "remove_profile",
            "check_connection",
            "disconnect",
            "invalidate_profile",
            "list_scopes",
            "cancel_scope_reads",
            "default_scope",
            "select_scope",
        ]),
    ))
    .expect("desktop build configuration is invalid");
}
