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
    // Tauri embeds platform icons in the native binary. Track the generated
    // resources explicitly so `tauri dev` rebuilds after the brand icon changes.
    for icon in ["icons/icon.icns", "icons/icon.ico", "icons/icon.png"] {
        println!("cargo:rerun-if-changed={icon}");
    }
    build_sparkle_bridge();
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
            "set_menu_language",
            "local_diagnostics",
            "connect_local_server",
            "ops_references",
            "ops_inspect",
            "ops_config_validate",
            "ops_preflight",
            "ops_apply",
            "ops_operation_status",
            "ops_cancel",
            "ops_resume",
            "remember_memory",
            "search_memory",
            "atomic_memory_entry",
            "cancel_memory_reads",
            "observe_runtime_liveness",
            "observe_runtime_capabilities",
            "probe_runtime_readiness",
            "cancel_runtime_reads",
            "get_stats",
            "cancel_stats_reads",
            "desktop_state",
            "save_profile",
            "remove_profile",
            "check_connection",
            "retry_connection",
            "probe_readiness",
            "disconnect",
            "invalidate_profile",
            "list_scopes",
            "cancel_scope_reads",
            "default_scope",
            "select_scope",
            "session_summary",
            "reset_session_observations",
            "preview_session_diagnostics",
        ]),
    ))
    .expect("desktop build configuration is invalid");
}

fn build_sparkle_bridge() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("macos") {
        return;
    }

    println!("cargo:rerun-if-changed=src/sparkle_bridge.m");
    println!("cargo:rerun-if-changed=src/sparkle_bridge_stub.c");
    println!("cargo:rerun-if-changed=Sparkle.framework");

    let manifest = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let framework = manifest.join("Sparkle.framework");
    let profile = std::env::var("PROFILE").unwrap_or_default();
    let out = std::path::PathBuf::from(std::env::var_os("OUT_DIR").unwrap());
    let object = out.join("sparkle_bridge.o");
    let archive = out.join("libpowercontext_sparkle_bridge.a");

    let source = if framework.is_dir() {
        println!("cargo:rustc-link-search=framework={}", manifest.display());
        println!("cargo:rustc-link-lib=framework=Sparkle");
        println!("cargo:rustc-link-arg=-Wl,-rpath,@executable_path/../Frameworks");
        manifest.join("src/sparkle_bridge.m")
    } else {
        if profile == "release" {
            panic!(
                "Sparkle.framework is required for a macOS release; run `bash desktop/scripts/setup-sparkle.sh`"
            );
        }
        println!(
            "cargo:warning=Sparkle.framework is absent; native updates are disabled in this debug build"
        );
        manifest.join("src/sparkle_bridge_stub.c")
    };

    let mut clang = std::process::Command::new("xcrun");
    clang.args(["clang", "-c", "-o"]).arg(&object).arg(&source);
    if framework.is_dir() {
        clang.args(["-fobjc-arc", "-mmacosx-version-min=12.0"]);
        clang.arg(format!("-F{}", manifest.display()));
    }
    let status = clang.status().expect("failed to invoke Apple clang");
    assert!(status.success(), "failed to compile the Sparkle bridge");

    let status = std::process::Command::new("xcrun")
        .args(["ar", "rcs"])
        .arg(&archive)
        .arg(&object)
        .status()
        .expect("failed to invoke Apple ar");
    assert!(status.success(), "failed to archive the Sparkle bridge");
    println!("cargo:rustc-link-search=native={}", out.display());
    println!("cargo:rustc-link-lib=static=powercontext_sparkle_bridge");
}
