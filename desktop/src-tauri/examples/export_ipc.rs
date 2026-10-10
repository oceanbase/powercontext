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

use powercontext_desktop::{
    connections::{
        profiles::{Authentication, CredentialState, ProfileInput, ProfileView},
        session::{
            ActiveView, CheckReport, CompatibilityProfile, DesktopState, Fact, MemoryContext,
            WriteOutcome, WriteRecord, WriteStatus,
        },
    },
    credentials::{CredentialWriteReceipt, CredentialWriteRequest, StorageChoice},
    diagnostics::{DiagnosticItem, DiagnosticKind, DiagnosticReport, HostDiagnostic},
    error::SafeError,
    ipc::FoundationInfo,
    transport::{ApiFailure, wire},
};
use ts_rs::TS;
fn main() {
    let config = ts_rs::Config::default().with_large_int("number");
    let license = include_str!("export_ipc.rs")
        .split(" */")
        .next()
        .unwrap()
        .to_owned()
        + " */\n\n";
    let mut output = license
        + &format!(
            "// Generated from Rust IPC types. Do not edit.\nexport {}\nexport {}\nexport {}\nexport {}\nexport {}\n",
            SafeError::decl(&config),
            FoundationInfo::decl(&config),
            StorageChoice::decl(&config),
            CredentialWriteRequest::decl(&config),
            CredentialWriteReceipt::decl(&config)
        );
    for declaration in [
        DiagnosticKind::decl(&config),
        DiagnosticItem::decl(&config),
        HostDiagnostic::decl(&config),
        DiagnosticReport::decl(&config),
        Authentication::decl(&config),
        ProfileView::decl(&config),
        ProfileInput::decl(&config),
        CredentialState::decl(&config),
        Fact::<String>::decl(&config),
        CheckReport::decl(&config),
        CompatibilityProfile::decl(&config),
        ActiveView::decl(&config),
        MemoryContext::decl(&config),
        WriteStatus::decl(&config),
        WriteRecord::decl(&config),
        WriteOutcome::decl(&config),
        DesktopState::decl(&config),
        ApiFailure::decl(&config),
    ]
    .into_iter()
    .chain(wire::declarations(&config))
    {
        output.push_str(&format!("export {declaration}\n"));
    }
    let output = output
        .lines()
        .map(str::trim_end)
        .collect::<Vec<_>>()
        .join("\n")
        + "\n";
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../ui/src/generated/ipc.ts");
    if std::env::args().any(|arg| arg == "--check") {
        assert_eq!(
            std::fs::read_to_string(path).unwrap().replace("\r\n", "\n"),
            output,
            "IPC drift: run cargo run --example export_ipc"
        );
    } else {
        std::fs::write(path, output).unwrap();
    }
}
