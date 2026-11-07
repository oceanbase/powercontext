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

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use ts_rs::TS;

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsOperation {
    Register,
    Start,
    Stop,
    Restart,
    RemoveRegistration,
    ConfigApply,
    Install,
    Repair,
}

impl OpsOperation {
    pub(super) fn wire_name(self) -> &'static str {
        match self {
            Self::Register => "register",
            Self::Start => "start",
            Self::Stop => "stop",
            Self::Restart => "restart",
            Self::RemoveRegistration => "remove_registration",
            Self::ConfigApply => "config_apply",
            Self::Install => "install",
            Self::Repair => "repair",
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsOutcome {
    Succeeded,
    Pending,
    Cancelled,
    Failed,
    PartialFailure,
    Uncertain,
    Rejected,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsBridgeError {
    Unavailable,
    UnsupportedPlatform,
    IntegrityFailure,
    InvalidInput,
    InvalidResponse,
    ProcessFailure,
    Timeout,
    OutputTooLarge,
    Storage,
    Busy,
    ShuttingDown,
    StaleApproval,
    UnknownOperation,
    Forbidden,
    ForeignBinding,
    ReferenceLimit,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsErrorCode {
    AttemptConflict,
    AttemptLimit,
    AuthorizationRequired,
    ConfigurationConflict,
    ConfigurationInvalid,
    ConfigurationNotLocal,
    ConfigurationNotOwned,
    ConfigurationPathUnsupported,
    DatabaseBackendUnsupported,
    DatabaseIncompatible,
    DatabaseCompatibilityUnknown,
    DatabaseRecoveryRequired,
    DatabaseMigrationRequired,
    DatabaseConfigurationMismatch,
    DatabaseSnapshotTooLarge,
    DatabaseSnapshotSpaceInsufficient,
    DatabaseAdmissionTimeout,
    DatabaseChangedDuringAdmission,
    InvalidInputOrBinding,
    ResourceArchiveInvalid,
    ResourceCatalogInvalid,
    ResourcePrepareFailed,
    ResourceProtocolUnsupported,
    ResourceTimeout,
    ResourceUnavailable,
    ResourceUnsafePath,
    ResourceCancelled,
    ResourceCorrupt,
    ResourceDownloadFailed,
    ResourceInvalid,
    ResourcePathUnsupported,
    ResourcePlatformUnsupported,
    ResourceSourceInvalid,
    DatabaseLocationUnsupported,
    ExistingIndependent,
    InstallationMissing,
    InstallationRecordInvalid,
    InsufficientSpaceOrPermissions,
    InvalidManagedBinding,
    InvalidRequest,
    InvalidTargetBinding,
    LayoutInvalid,
    LockingUnsupported,
    ManagedInstallationUnavailable,
    OperationBusy,
    OperationCompleted,
    OperationConflict,
    OperationMissing,
    OperationSuperseded,
    OptionUnsupported,
    OutcomeUncertain,
    OwnershipConflict,
    PlanInvalid,
    PlanStale,
    PortConflict,
    ProgressUnavailable,
    RecordInvalid,
    RecordUnsupported,
    RegistrationMissing,
    RepairOwnershipConflict,
    RepairRequired,
    RequestTooLarge,
    ResourceIdentityChanged,
    ResourceUntrusted,
    ResponseTooLarge,
    RuntimeDamaged,
    TargetConflict,
    TargetInvalid,
    TargetUnsupported,
    UnsupportedAction,
    UnsupportedPlatform,
    UnsupportedProtocol,
    CheckpointFailed,
    InspectionFailed,
    OperationFailed,
    Unknown,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsAction {
    Inspect,
    ConfigValidate,
    Preflight,
    Apply,
    OperationStatus,
    Cancel,
    Resume,
}

impl OpsAction {
    pub(super) fn wire_name(self) -> &'static str {
        match self {
            Self::Inspect => "inspect",
            Self::ConfigValidate => "config_validate",
            Self::Preflight => "preflight",
            Self::Apply => "apply",
            Self::OperationStatus => "operation_status",
            Self::Cancel => "cancel",
            Self::Resume => "resume",
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct OpsOptions {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub http_port: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub dashboard_enabled: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub start_on_login: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub restart: Option<bool>,
}

impl OpsOptions {
    pub(super) fn validate(&self, operation: OpsOperation) -> Result<(), OpsBridgeError> {
        let allowed = match operation {
            OpsOperation::Register => [false, false, true, false],
            OpsOperation::ConfigApply => [true, true, true, true],
            OpsOperation::Install => [true, false, true, false],
            _ => [false, false, false, false],
        };
        if (!allowed[0] && self.http_port.is_some())
            || (!allowed[1] && self.dashboard_enabled.is_some())
            || (!allowed[2] && self.start_on_login.is_some())
            || (!allowed[3] && self.restart.is_some())
            || self.http_port == Some(0)
        {
            return Err(OpsBridgeError::InvalidInput);
        }
        Ok(())
    }

    pub(super) fn wire_value(&self) -> Value {
        let mut value = Map::new();
        for (key, item) in [
            ("http_port", self.http_port.map(Value::from)),
            ("dashboard_enabled", self.dashboard_enabled.map(Value::from)),
            ("start_on_login", self.start_on_login.map(Value::from)),
            ("restart", self.restart.map(Value::from)),
        ] {
            if let Some(item) = item {
                value.insert(key.into(), item);
            }
        }
        Value::Object(value)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct OpsResumeReference {
    pub operation_id: String,
    pub expected_attempt: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct OpsPreflightInput {
    pub operation: OpsOperation,
    #[serde(default)]
    pub options: OpsOptions,
    #[serde(default)]
    #[ts(optional)]
    pub resume: Option<OpsResumeReference>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct OpsApprovalInput {
    pub approval_id: String,
    pub plan_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct OpsStatusInput {
    pub operation_id: String,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct OpsCancelInput {
    pub operation_id: String,
    pub attempt: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
pub struct OpsOperationReference {
    pub operation_id: String,
    pub binding_status: OpsReferenceBindingStatus,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsReferenceBindingStatus {
    Current,
    Different,
    Unavailable,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsReferences {
    pub binding_available: bool,
    pub operations: Vec<OpsOperationReference>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsInspectionReply {
    pub outcome: OpsOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub components: Option<OpsComponents>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub error: Option<OpsErrorCode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub bridge_error: Option<OpsBridgeError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub exit_code: Option<i32>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsValidationReply {
    pub outcome: OpsOutcome,
    pub configuration: OpsConfigurationState,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub error: Option<OpsErrorCode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub bridge_error: Option<OpsBridgeError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub exit_code: Option<i32>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsConfigurationState {
    Valid,
    MissingOrInvalid,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsPlanApproval {
    pub approval_id: String,
    pub plan_id: String,
    pub operation: OpsOperation,
    pub options: OpsOptions,
    pub target: OpsTargetSummary,
    pub effects: OpsPlanEffects,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub installation: Option<OpsInstallationSummary>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsPreflightReply {
    pub outcome: OpsOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub approval: Option<OpsPlanApproval>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub error: Option<OpsErrorCode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub bridge_error: Option<OpsBridgeError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub exit_code: Option<i32>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsTargetSummary {
    pub target_id: String,
    pub python_executable: String,
    pub package_version: String,
    pub env_file: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub endpoint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub data_dir: Option<String>,
    pub start_on_login: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsPlanEffects {
    pub may_start: bool,
    pub preserves_data: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsInstallationSummary {
    pub classification: OpsInstallationClassification,
    pub resources: OpsInstallationResources,
    pub database: OpsDatabaseClassification,
    pub space_required: u64,
    pub verify_running: bool,
    pub preserve_stopped: bool,
    pub registration_present: bool,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsInstallationClassification {
    Missing,
    OwnedCurrent,
    OwnedRepairable,
    PendingOperation,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsInstallationResources {
    Missing,
    Damaged,
    Verified,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum OpsDatabaseClassification {
    New,
    ExistingUnknown,
    ExistingReady,
    Incompatible,
    Unknown,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsComponents {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub support: Option<OpsSupportState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub registration: Option<OpsRegistrationState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub definition: Option<OpsDefinitionState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub manager_ownership: Option<OpsManagerOwnership>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub manager: Option<OpsManagerState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub liveness: Option<OpsLivenessState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub readiness: Option<OpsReadinessState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub endpoint: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub maintenance: Option<OpsMaintenancePhase>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub resources: Option<OpsRuntimeResources>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub configuration: Option<OpsConfigurationState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub installation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub resource_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub resource_digest: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub database: Option<OpsDatabaseClassification>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub connection_compatibility: Option<OpsConnectionCompatibility>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub installation_stage: Option<OpsServicePhase>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub installation_observation: Option<OpsObservationState>,
}

macro_rules! status_enum {
    ($name:ident { $($variant:ident => $wire:literal),+ $(,)? }) => {
        #[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize, TS)]
        #[serde(rename_all = "snake_case")]
        pub enum $name { $($variant),+ }

    };
}

status_enum!(OpsMaintenancePhase { ManualStopped => "manual_stopped", StartFailed => "start_failed", ConfigurationSwitching => "configuration_switching", ManualSwitching => "manual_switching", RuntimeRepair => "runtime_repair", Stopping => "stopping", Stopped => "stopped", Switching => "switching", DatabaseReady => "database_ready" });
status_enum!(OpsRuntimeResources { Present => "present", MissingExecutable => "missing_executable" });
status_enum!(OpsConnectionCompatibility { NotChecked => "not_checked" });
status_enum!(OpsSupportState { Supported => "supported", Unsupported => "unsupported", Unknown => "unknown" });
status_enum!(OpsRegistrationState { Installed => "installed", NotInstalled => "not_installed", Invalid => "invalid", Unknown => "unknown" });
status_enum!(OpsDefinitionState { Current => "current", Stale => "stale", MissingExecutable => "missing_executable", Unknown => "unknown" });
status_enum!(OpsManagerOwnership { NotLoaded => "not_loaded", Owned => "owned", Foreign => "foreign", Unknown => "unknown" });
status_enum!(OpsManagerState { Active => "active", Inactive => "inactive", Failed => "failed", Unknown => "unknown" });
status_enum!(OpsLivenessState { Live => "live", Unreachable => "unreachable", Unknown => "unknown" });
status_enum!(OpsReadinessState { Failed => "failed", NotProbed => "not_probed", VerifiedDuringOperation => "verified_during_operation", Unknown => "unknown" });
status_enum!(OpsServicePhase { Accepted => "accepted", RegistrationDispatching => "registration_dispatching", Service => "service", Preparing => "preparing", Prepared => "prepared", Publishing => "publishing", ResourcesPublished => "resources_published", ConfigurationPrepared => "configuration_prepared", Configured => "configured", Registering => "registering", Complete => "complete" });
status_enum!(OpsAttemptPhase { Accepted => "accepted", Executing => "executing", Finished => "finished" });
status_enum!(OpsExecutionState { Finished => "finished", Interrupted => "interrupted", Unknown => "unknown" });
status_enum!(OpsExecutorObservation { Exited => "exited", NotConfirmedExited => "not_confirmed_exited", Unknown => "unknown" });
status_enum!(OpsObservationState { Unknown => "unknown" });
status_enum!(OpsRecoveryAction { Inspect => "inspect", Preflight => "preflight", OperationStatus => "operation_status" });

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsProgress {
    pub request_id: String,
    pub operation_id: String,
    pub attempt: u32,
    pub sequence: Option<u32>,
    pub phase: Option<OpsServicePhase>,
    pub accepted: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsOperationReply {
    pub operation_id: String,
    pub outcome: OpsOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub attempt: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub exit_code: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub error: Option<OpsErrorCode>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub bridge_error: Option<OpsBridgeError>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub status: Option<OpsOperationStatus>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsOperationStatus {
    pub operation_id: String,
    pub attempt: u32,
    pub phase: OpsAttemptPhase,
    pub executor: OpsExecutor,
    pub execution_state: OpsExecutionState,
    pub outcome: OpsOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub cancel_requested: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub result: Option<OpsStoredResult>,
    pub observation: OpsObservation,
    pub history: Vec<OpsAttemptSummary>,
    pub recovery_actions: Vec<OpsRecoveryAction>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsExecutor {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub pid: Option<u64>,
    pub observation: OpsExecutorObservation,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsObservation {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub state: Option<OpsObservationState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub components: Option<OpsComponents>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub error: Option<OpsErrorCode>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsAttemptSummary {
    pub attempt: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub outcome: Option<OpsOutcome>,
}

#[derive(Clone, Debug, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsStoredResult {
    pub outcome: OpsOutcome,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub components: Option<OpsComponents>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub error: Option<OpsErrorCode>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub recovery_actions: Vec<OpsRecoveryAction>,
}
