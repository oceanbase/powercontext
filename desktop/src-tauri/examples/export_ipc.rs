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
        discovery::{
            CompatibilityReason, CompatibilityStatus, DiscoveryResult, DiscoveryStatus,
            OperationCompatibility,
        },
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
    ops_bridge::*,
    session_observations::{
        ObservationOutcome, ObservationUnit, ObservedOperation, OperationSummary, OutcomeCounts,
        SessionCoverage, SessionObservation, SessionSummary,
    },
    transport::{
        ApiFailure, AtomicMemoryDetail, MemorySaveResponse, MemorySearchHit, MemorySearchResponse,
        wire,
    },
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
        OpsOperation::decl(&config),
        OpsOutcome::decl(&config),
        OpsBridgeError::decl(&config),
        OpsErrorCode::decl(&config),
        OpsAction::decl(&config),
        OpsOptions::decl(&config),
        OpsResumeReference::decl(&config),
        OpsPreflightInput::decl(&config),
        OpsApprovalInput::decl(&config),
        OpsStatusInput::decl(&config),
        OpsCancelInput::decl(&config),
        OpsOperationReference::decl(&config),
        OpsReferenceBindingStatus::decl(&config),
        OpsReferences::decl(&config),
        OpsInspectionReply::decl(&config),
        OpsValidationReply::decl(&config),
        OpsConfigurationState::decl(&config),
        OpsPlanApproval::decl(&config),
        OpsPreflightReply::decl(&config),
        OpsTargetSummary::decl(&config),
        OpsPlanEffects::decl(&config),
        OpsInstallationSummary::decl(&config),
        OpsInstallationClassification::decl(&config),
        OpsInstallationResources::decl(&config),
        OpsDatabaseClassification::decl(&config),
        OpsComponents::decl(&config),
        OpsProgress::decl(&config),
        OpsOperationReply::decl(&config),
        OpsOperationStatus::decl(&config),
        OpsExecutor::decl(&config),
        OpsObservation::decl(&config),
        OpsAttemptSummary::decl(&config),
        OpsStoredResult::decl(&config),
        OpsMaintenancePhase::decl(&config),
        OpsRuntimeResources::decl(&config),
        OpsConnectionCompatibility::decl(&config),
        OpsSupportState::decl(&config),
        OpsRegistrationState::decl(&config),
        OpsDefinitionState::decl(&config),
        OpsManagerOwnership::decl(&config),
        OpsManagerState::decl(&config),
        OpsLivenessState::decl(&config),
        OpsReadinessState::decl(&config),
        OpsServicePhase::decl(&config),
        OpsAttemptPhase::decl(&config),
        OpsExecutionState::decl(&config),
        OpsExecutorObservation::decl(&config),
        OpsObservationState::decl(&config),
        OpsRecoveryAction::decl(&config),
        DiagnosticKind::decl(&config),
        DiagnosticItem::decl(&config),
        HostDiagnostic::decl(&config),
        DiagnosticReport::decl(&config),
        Authentication::decl(&config),
        ProfileView::decl(&config),
        ProfileInput::decl(&config),
        CredentialState::decl(&config),
        Fact::<String>::decl(&config),
        DiscoveryStatus::decl(&config),
        CompatibilityReason::decl(&config),
        CompatibilityStatus::decl(&config),
        OperationCompatibility::decl(&config),
        DiscoveryResult::decl(&config),
        CheckReport::decl(&config),
        CompatibilityProfile::decl(&config),
        ActiveView::decl(&config),
        MemoryContext::decl(&config),
        WriteStatus::decl(&config),
        WriteRecord::decl(&config),
        WriteOutcome::decl(&config),
        DesktopState::decl(&config),
        ApiFailure::decl(&config),
        MemorySaveResponse::decl(&config),
        MemorySearchHit::decl(&config),
        MemorySearchResponse::decl(&config),
        AtomicMemoryDetail::decl(&config),
        ObservationUnit::decl(&config),
        ObservedOperation::decl(&config),
        ObservationOutcome::decl(&config),
        SessionObservation::decl(&config),
        OutcomeCounts::decl(&config),
        OperationSummary::decl(&config),
        SessionCoverage::decl(&config),
        SessionSummary::decl(&config),
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
    assert!(
        !output.contains("?:  |"),
        "IPC generation produced an empty TypeScript field type",
    );
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
