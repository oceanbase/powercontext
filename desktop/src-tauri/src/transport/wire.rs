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

// Generated from openapi/powercontext.yaml. Do not edit.
// rustfmt uses this generated layout verbatim.
use serde::{Deserialize, Serialize};
use ts_rs::TS;

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum AccessAction {
    #[serde(rename = "server.observe")]
    #[default]
    ServerObserve,
    #[serde(rename = "server.admin")]
    ServerAdmin,
    #[serde(rename = "scope.read")]
    ScopeRead,
    #[serde(rename = "scope.contribute")]
    ScopeContribute,
    #[serde(rename = "scope.review")]
    ScopeReview,
    #[serde(rename = "scope.delegate")]
    ScopeDelegate,
    #[serde(rename = "scope.admin")]
    ScopeAdmin,
    #[serde(rename = "artifact.read")]
    ArtifactRead,
    #[serde(rename = "artifact.write")]
    ArtifactWrite,
    #[serde(rename = "artifact.share")]
    ArtifactShare,
    #[serde(rename = "handoff.evidence.inspect")]
    HandoffEvidenceInspect,
    #[serde(rename = "handoff.acknowledge")]
    HandoffAcknowledge,
    #[serde(rename = "prompt.use")]
    PromptUse,
}

impl std::fmt::Display for AccessAction {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::ServerObserve => write!(f, "server.observe"),
            Self::ServerAdmin => write!(f, "server.admin"),
            Self::ScopeRead => write!(f, "scope.read"),
            Self::ScopeContribute => write!(f, "scope.contribute"),
            Self::ScopeReview => write!(f, "scope.review"),
            Self::ScopeDelegate => write!(f, "scope.delegate"),
            Self::ScopeAdmin => write!(f, "scope.admin"),
            Self::ArtifactRead => write!(f, "artifact.read"),
            Self::ArtifactWrite => write!(f, "artifact.write"),
            Self::ArtifactShare => write!(f, "artifact.share"),
            Self::HandoffEvidenceInspect => write!(f, "handoff.evidence.inspect"),
            Self::HandoffAcknowledge => write!(f, "handoff.acknowledge"),
            Self::PromptUse => write!(f, "prompt.use"),
        }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum AccessControlMode {
    #[serde(rename = "disabled")]
    #[default]
    Disabled,
    #[serde(rename = "enforced")]
    Enforced,
}

impl std::fmt::Display for AccessControlMode {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Disabled => write!(f, "disabled"),
            Self::Enforced => write!(f, "enforced"),
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct AccessMeResponse {
    #[serde(rename = "principal")]
    pub principal: AccessPrincipal,
    #[serde(rename = "mode")]
    pub mode: AccessControlMode,
    #[serde(rename = "resource_kinds")]
    pub resource_kinds: Vec<AccessResourceType>,
    #[serde(rename = "provider_capabilities")]
    pub provider_capabilities: AccessProviderCapabilities,
    #[serde(rename = "artifact_families")]
    pub artifact_families: Vec<ArtifactFamilyAccessCapability>,
}

impl AccessMeResponse {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        principal: AccessPrincipal,
        mode: AccessControlMode,
        resource_kinds: Vec<AccessResourceType>,
        provider_capabilities: AccessProviderCapabilities,
        artifact_families: Vec<ArtifactFamilyAccessCapability>,
    ) -> AccessMeResponse {
        AccessMeResponse {
            principal,
            mode,
            resource_kinds,
            provider_capabilities,
            artifact_families,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct AccessPrincipal {
    #[serde(rename = "type")]
    pub r#type: Type,
    #[serde(rename = "id")]
    pub id: String,
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "description",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub description: Option<Option<String>>,
}

impl AccessPrincipal {
    #[allow(clippy::too_many_arguments)]
    pub fn new(r#type: Type, id: String) -> AccessPrincipal {
        AccessPrincipal {
            r#type,
            id,
            description: None,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum Type {
    #[serde(rename = "user")]
    #[default]
    User,
    #[serde(rename = "service")]
    Service,
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct AccessProviderCapabilities {
    #[serde(rename = "safe_resource_filtering")]
    pub safe_resource_filtering: bool,
    #[serde(rename = "multi_requirement_check")]
    pub multi_requirement_check: bool,
    #[serde(rename = "relationship_management")]
    pub relationship_management: bool,
    #[serde(rename = "group_subjects")]
    pub group_subjects: bool,
    #[serde(rename = "multi_principal")]
    pub multi_principal: bool,
    #[serde(rename = "max_direct_resource_keys")]
    pub max_direct_resource_keys: i64,
}

impl AccessProviderCapabilities {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        safe_resource_filtering: bool,
        multi_requirement_check: bool,
        relationship_management: bool,
        group_subjects: bool,
        multi_principal: bool,
        max_direct_resource_keys: i64,
    ) -> AccessProviderCapabilities {
        AccessProviderCapabilities {
            safe_resource_filtering,
            multi_requirement_check,
            relationship_management,
            group_subjects,
            multi_principal,
            max_direct_resource_keys,
        }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum AccessResourceType {
    #[serde(rename = "server")]
    #[default]
    Server,
    #[serde(rename = "scope")]
    Scope,
    #[serde(rename = "artifact")]
    Artifact,
}

impl std::fmt::Display for AccessResourceType {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Server => write!(f, "server"),
            Self::Scope => write!(f, "scope"),
            Self::Artifact => write!(f, "artifact"),
        }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum AccessRole {
    #[serde(rename = "handoff.viewer")]
    #[default]
    HandoffViewer,
    #[serde(rename = "handoff.receiver")]
    HandoffReceiver,
    #[serde(rename = "artifact.viewer")]
    ArtifactViewer,
    #[serde(rename = "prompt.user")]
    PromptUser,
    #[serde(rename = "artifact.owner")]
    ArtifactOwner,
    #[serde(rename = "scope.viewer")]
    ScopeViewer,
    #[serde(rename = "scope.contributor")]
    ScopeContributor,
    #[serde(rename = "scope.reviewer")]
    ScopeReviewer,
    #[serde(rename = "scope.delegator")]
    ScopeDelegator,
    #[serde(rename = "scope.admin")]
    ScopeAdmin,
    #[serde(rename = "server.observer")]
    ServerObserver,
    #[serde(rename = "server.admin")]
    ServerAdmin,
}

impl std::fmt::Display for AccessRole {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::HandoffViewer => write!(f, "handoff.viewer"),
            Self::HandoffReceiver => write!(f, "handoff.receiver"),
            Self::ArtifactViewer => write!(f, "artifact.viewer"),
            Self::PromptUser => write!(f, "prompt.user"),
            Self::ArtifactOwner => write!(f, "artifact.owner"),
            Self::ScopeViewer => write!(f, "scope.viewer"),
            Self::ScopeContributor => write!(f, "scope.contributor"),
            Self::ScopeReviewer => write!(f, "scope.reviewer"),
            Self::ScopeDelegator => write!(f, "scope.delegator"),
            Self::ScopeAdmin => write!(f, "scope.admin"),
            Self::ServerObserver => write!(f, "server.observer"),
            Self::ServerAdmin => write!(f, "server.admin"),
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ArtifactDreamingOperationCapability {
    #[serde(rename = "operation")]
    pub operation: DreamOperation,
    #[serde(rename = "output_kind")]
    pub output_kind: OutputKind,
    #[serde(rename = "effect")]
    pub effect: Effect,
}

impl ArtifactDreamingOperationCapability {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        operation: DreamOperation,
        output_kind: OutputKind,
        effect: Effect,
    ) -> ArtifactDreamingOperationCapability {
        ArtifactDreamingOperationCapability {
            operation,
            output_kind,
            effect,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum OutputKind {
    #[serde(rename = "candidate")]
    #[default]
    Candidate,
    #[serde(rename = "tag_candidate")]
    TagCandidate,
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum Effect {
    #[serde(rename = "review_then_publish")]
    #[default]
    ReviewThenPublish,
    #[serde(rename = "review_then_publish_configuration")]
    ReviewThenPublishConfiguration,
    #[serde(rename = "review_then_replace_tags")]
    ReviewThenReplaceTags,
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ArtifactFamilyAccessCapability {
    #[serde(rename = "family")]
    pub family: String,
    #[serde(rename = "enabled")]
    pub enabled: bool,
    #[serde(rename = "share_unit")]
    pub share_unit: ShareUnit,
    #[serde(rename = "actions")]
    pub actions: Vec<AccessAction>,
    #[serde(rename = "grantable_roles")]
    pub grantable_roles: Vec<AccessRole>,
}

impl ArtifactFamilyAccessCapability {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        family: String,
        enabled: bool,
        share_unit: ShareUnit,
        actions: Vec<AccessAction>,
        grantable_roles: Vec<AccessRole>,
    ) -> ArtifactFamilyAccessCapability {
        ArtifactFamilyAccessCapability {
            family,
            enabled,
            share_unit,
            actions,
            grantable_roles,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum ShareUnit {
    #[serde(rename = "artifact")]
    #[default]
    Artifact,
    #[serde(rename = "memory_entry")]
    MemoryEntry,
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum ArtifactReadFamily {
    #[serde(rename = "memory")]
    #[default]
    Memory,
    #[serde(rename = "atomic-memory")]
    AtomicMemory,
    #[serde(rename = "experience")]
    Experience,
    #[serde(rename = "skill")]
    Skill,
    #[serde(rename = "handoff")]
    Handoff,
    #[serde(rename = "profile")]
    Profile,
    #[serde(rename = "prompt")]
    Prompt,
    #[serde(rename = "topic-memory")]
    TopicMemory,
}

impl std::fmt::Display for ArtifactReadFamily {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Memory => write!(f, "memory"),
            Self::AtomicMemory => write!(f, "atomic-memory"),
            Self::Experience => write!(f, "experience"),
            Self::Skill => write!(f, "skill"),
            Self::Handoff => write!(f, "handoff"),
            Self::Profile => write!(f, "profile"),
            Self::Prompt => write!(f, "prompt"),
            Self::TopicMemory => write!(f, "topic-memory"),
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ArtifactReference {
    #[serde(rename = "family")]
    pub family: String,
    #[serde(rename = "artifact_id")]
    pub artifact_id: String,
    #[serde(rename = "revision")]
    pub revision: i64,
}

impl ArtifactReference {
    #[allow(clippy::too_many_arguments)]
    pub fn new(family: String, artifact_id: String, revision: i64) -> ArtifactReference {
        ArtifactReference {
            family,
            artifact_id,
            revision,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ArtifactRevision {
    #[serde(rename = "scope_id")]
    pub scope_id: String,
    #[serde(rename = "family")]
    pub family: ArtifactReadFamily,
    #[serde(rename = "artifact_id")]
    pub artifact_id: String,
    #[serde(rename = "revision")]
    pub revision: i64,
    #[ts(type = "Record<string, unknown>")]
    #[serde(rename = "content")]
    pub content: std::collections::HashMap<String, serde_json::Value>,
    #[serde(rename = "sources")]
    pub sources: Vec<SourceTypeReference>,
    #[serde(rename = "artifacts")]
    pub artifacts: Vec<ArtifactReference>,
    #[serde(rename = "content_digest")]
    pub content_digest: String,
    /// When this revision was committed. Absent for revisions stored before this field existed.
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "created_at",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub created_at: Option<Option<String>>,
    /// Principal that committed a rollback. Ordinary revisions omit it.
    #[ts(optional)]
    #[ts(type = "AccessPrincipal | null")]
    #[serde(
        rename = "created_by",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub created_by: Option<Option<AccessPrincipal>>,
    /// Historical revision restored by this rollback.
    #[ts(optional)]
    #[ts(type = "number | null")]
    #[serde(
        rename = "restored_from_revision",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub restored_from_revision: Option<Option<i64>>,
    /// Reason recorded for a rollback.
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "reason",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub reason: Option<Option<String>>,
}

impl ArtifactRevision {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        scope_id: String,
        family: ArtifactReadFamily,
        artifact_id: String,
        revision: i64,
        content: std::collections::HashMap<String, serde_json::Value>,
        sources: Vec<SourceTypeReference>,
        artifacts: Vec<ArtifactReference>,
        content_digest: String,
    ) -> ArtifactRevision {
        ArtifactRevision {
            scope_id,
            family,
            artifact_id,
            revision,
            content,
            sources,
            artifacts,
            content_digest,
            created_at: None,
            created_by: None,
            restored_from_revision: None,
            reason: None,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct AtomicMemoryRecord {
    #[serde(rename = "artifact")]
    pub artifact: ArtifactReference,
    #[serde(rename = "kind")]
    pub kind: String,
    #[serde(rename = "text")]
    pub text: String,
    #[serde(rename = "state")]
    pub state: AtomicMemoryState,
    #[serde(rename = "state_version")]
    pub state_version: i64,
    #[ts(type = "string | null")]
    #[serde(rename = "merged_into_id", deserialize_with = "Option::deserialize")]
    pub merged_into_id: Option<String>,
}

impl AtomicMemoryRecord {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        artifact: ArtifactReference,
        kind: String,
        text: String,
        state: AtomicMemoryState,
        state_version: i64,
        merged_into_id: Option<String>,
    ) -> AtomicMemoryRecord {
        AtomicMemoryRecord {
            artifact,
            kind,
            text,
            state,
            state_version,
            merged_into_id,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct AtomicMemorySearchHit {
    #[serde(rename = "memory")]
    pub memory: AtomicMemoryRecord,
    /// Raw RRF score. Hybrid mode applies the deployment's normalized relative FTS/vector weights.
    #[serde(rename = "score")]
    pub score: f64,
    #[serde(rename = "matched_by")]
    pub matched_by: Vec<MatchedBy>,
}

impl AtomicMemorySearchHit {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        memory: AtomicMemoryRecord,
        score: f64,
        matched_by: Vec<MatchedBy>,
    ) -> AtomicMemorySearchHit {
        AtomicMemorySearchHit {
            memory,
            score,
            matched_by,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum MatchedBy {
    #[serde(rename = "text")]
    #[default]
    Text,
    #[serde(rename = "vector")]
    Vector,
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum AtomicMemorySearchMode {
    #[serde(rename = "text")]
    #[default]
    Text,
    #[serde(rename = "vector")]
    Vector,
    #[serde(rename = "hybrid")]
    Hybrid,
}

impl std::fmt::Display for AtomicMemorySearchMode {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Text => write!(f, "text"),
            Self::Vector => write!(f, "vector"),
            Self::Hybrid => write!(f, "hybrid"),
        }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum AtomicMemoryState {
    #[serde(rename = "active")]
    #[default]
    Active,
    #[serde(rename = "forgotten")]
    Forgotten,
    #[serde(rename = "merged")]
    Merged,
    #[serde(rename = "retired")]
    Retired,
}

impl std::fmt::Display for AtomicMemoryState {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Active => write!(f, "active"),
            Self::Forgotten => write!(f, "forgotten"),
            Self::Merged => write!(f, "merged"),
            Self::Retired => write!(f, "retired"),
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct Capabilities {
    #[ts(optional)]
    #[serde(rename = "prompts", skip_serializing_if = "Option::is_none")]
    pub prompts: Option<std::collections::HashMap<String, PromptCapability>>,
    /// Whether asynchronous Artifact Dream execution is configured.
    #[ts(optional)]
    #[serde(rename = "artifact_dreaming", skip_serializing_if = "Option::is_none")]
    pub artifact_dreaming: Option<bool>,
    /// Dream operations enabled by the current Runtime, including their reviewed effect.
    #[ts(optional)]
    #[serde(
        rename = "artifact_dreaming_operations",
        skip_serializing_if = "Option::is_none"
    )]
    pub artifact_dreaming_operations: Option<Vec<ArtifactDreamingOperationCapability>>,
    #[serde(rename = "source_types")]
    pub source_types: Vec<String>,
    #[serde(rename = "artifact_families")]
    pub artifact_families: Vec<String>,
    /// Whether pending Sources can be extracted into Memory.
    #[serde(rename = "memory_extraction")]
    pub memory_extraction: bool,
    #[ts(optional)]
    #[serde(rename = "extraction", skip_serializing_if = "Option::is_none")]
    pub extraction: Option<ExtractionStatus>,
    /// Whether the configured model can generate reviewed Experience Candidates.
    #[ts(optional)]
    #[serde(
        rename = "experience_generation",
        skip_serializing_if = "Option::is_none"
    )]
    pub experience_generation: Option<bool>,
    /// Whether the configured model can generate reviewed managed Skill Candidates.
    #[ts(optional)]
    #[serde(
        rename = "managed_skill_generation",
        skip_serializing_if = "Option::is_none"
    )]
    pub managed_skill_generation: Option<bool>,
    /// Whether host-local external Skill discovery and exact resolution are configured.
    #[ts(optional)]
    #[serde(
        rename = "external_skill_registry",
        skip_serializing_if = "Option::is_none"
    )]
    pub external_skill_registry: Option<bool>,
    /// Whether exact evidence can be generated into an inspectable Handoff Draft.
    #[serde(rename = "handoff_generation")]
    pub handoff_generation: bool,
    #[serde(rename = "search_modes")]
    pub search_modes: Vec<MemorySearchMode>,
    #[serde(rename = "context_versions")]
    pub context_versions: Vec<PreparedContextSchema>,
}

impl Capabilities {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        source_types: Vec<String>,
        artifact_families: Vec<String>,
        memory_extraction: bool,
        handoff_generation: bool,
        search_modes: Vec<MemorySearchMode>,
        context_versions: Vec<PreparedContextSchema>,
    ) -> Capabilities {
        Capabilities {
            prompts: None,
            artifact_dreaming: None,
            artifact_dreaming_operations: None,
            source_types,
            artifact_families,
            memory_extraction,
            extraction: None,
            experience_generation: None,
            managed_skill_generation: None,
            external_skill_registry: None,
            handoff_generation,
            search_modes,
            context_versions,
        }
    }
}

/// ContractVersion : A major/minor compatibility version. A major increment may remove or incompatibly change the governed contract. A minor increment only adds backward-compatible behavior or fields.
#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct ContractVersion {
    #[serde(rename = "major")]
    pub major: i64,
    #[serde(rename = "minor")]
    pub minor: i64,
}

impl ContractVersion {
    /// A major/minor compatibility version. A major increment may remove or incompatibly change the governed contract. A minor increment only adds backward-compatible behavior or fields.
    #[allow(clippy::too_many_arguments)]
    pub fn new(major: i64, minor: i64) -> ContractVersion {
        ContractVersion { major, minor }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum DreamOperation {
    #[serde(rename = "refine_experience")]
    #[default]
    RefineExperience,
    #[serde(rename = "derive_skill")]
    DeriveSkill,
    #[serde(rename = "revise_skill")]
    ReviseSkill,
    #[serde(rename = "revise_profile")]
    ReviseProfile,
    #[serde(rename = "revise_memory")]
    ReviseMemory,
    #[serde(rename = "revise_topic_memory")]
    ReviseTopicMemory,
    #[serde(rename = "refresh_handoff")]
    RefreshHandoff,
    #[serde(rename = "revise_prompt")]
    RevisePrompt,
    #[serde(rename = "revise_tags")]
    ReviseTags,
}

impl std::fmt::Display for DreamOperation {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::RefineExperience => write!(f, "refine_experience"),
            Self::DeriveSkill => write!(f, "derive_skill"),
            Self::ReviseSkill => write!(f, "revise_skill"),
            Self::ReviseProfile => write!(f, "revise_profile"),
            Self::ReviseMemory => write!(f, "revise_memory"),
            Self::ReviseTopicMemory => write!(f, "revise_topic_memory"),
            Self::RefreshHandoff => write!(f, "refresh_handoff"),
            Self::RevisePrompt => write!(f, "revise_prompt"),
            Self::ReviseTags => write!(f, "revise_tags"),
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ExtractionBackground {
    /// Placement of the Memory Supervisor. None means no background executor; synchronous flush may still work.
    #[serde(rename = "location")]
    pub location: Location,
    /// Current local Supervisor leadership role. Standby is normal; null means no running local Supervisor.
    #[ts(optional)]
    #[ts(type = "Role | null")]
    #[serde(
        rename = "role",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub role: Option<Option<Role>>,
    /// Local Supervisor lifecycle and control state, independent of individual worker outcomes. A running Supervisor may be retrying failed workers. External state is unknown.
    #[serde(rename = "state")]
    pub state: State,
    /// Whether this process schedules automatic Memory extraction. False still permits explicit flush and recovery of accepted work. Null means the external worker schedule is unknown.
    #[ts(optional)]
    #[ts(type = "boolean | null")]
    #[serde(
        rename = "automatic_processing_enabled",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub automatic_processing_enabled: Option<Option<bool>>,
}

impl ExtractionBackground {
    #[allow(clippy::too_many_arguments)]
    pub fn new(location: Location, state: State) -> ExtractionBackground {
        ExtractionBackground {
            location,
            role: None,
            state,
            automatic_processing_enabled: None,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum Location {
    #[serde(rename = "local")]
    #[default]
    Local,
    #[serde(rename = "external")]
    External,
    #[serde(rename = "none")]
    None,
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum Role {
    #[serde(rename = "leader")]
    #[default]
    Leader,
    #[serde(rename = "standby")]
    Standby,
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum State {
    #[serde(rename = "running")]
    #[default]
    Running,
    #[serde(rename = "degraded")]
    Degraded,
    #[serde(rename = "stopped")]
    Stopped,
    #[serde(rename = "unknown")]
    Unknown,
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ExtractionFailure {
    /// Sanitized category: model_configuration_error, model_timeout, model_unavailable, invalid_model_output, worker_timeout, worker_crash, invalid_worker_result, missing_durable_acknowledgement, supervisor_failed, lease_renewal_failed, scope_discovery_failed, or processing_failed. Raw exception messages, model inputs, and credentials are never returned.
    #[serde(rename = "code")]
    pub code: String,
    /// Inference for recognized model failures; otherwise the boundary where failure was observed. Flush or worker does not identify the failing internal component.
    #[serde(rename = "stage")]
    pub stage: Stage,
    /// UTC time when this process observed the failure.
    #[serde(rename = "occurred_at")]
    pub occurred_at: String,
}

impl ExtractionFailure {
    #[allow(clippy::too_many_arguments)]
    pub fn new(code: String, stage: Stage, occurred_at: String) -> ExtractionFailure {
        ExtractionFailure {
            code,
            stage,
            occurred_at,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum Stage {
    #[serde(rename = "inference")]
    #[default]
    Inference,
    #[serde(rename = "flush")]
    Flush,
    #[serde(rename = "worker")]
    Worker,
    #[serde(rename = "supervisor")]
    Supervisor,
    #[serde(rename = "lease_renewal")]
    LeaseRenewal,
    #[serde(rename = "scope_discovery")]
    ScopeDiscovery,
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ExtractionObservation {
    /// Unverified means no execution outcome or control failure has been observed in this window. Observed means at least one success or failure is recorded; neither value is a health verdict.
    #[serde(rename = "status")]
    pub status: ExtractionObservationStatus,
    /// UTC start of this Runtime's observation window. Records cover this process and its child Memory workers, reset on Runtime restart, and do not include remote workers or a durable per-Scope failure history.
    #[serde(rename = "since")]
    pub since: String,
    #[ts(optional)]
    #[serde(rename = "last_failure", skip_serializing_if = "Option::is_none")]
    pub last_failure: Option<ExtractionFailure>,
    /// UTC time of the most recent local successful nonempty synchronous flush or acknowledged Memory worker invocation. This does not prove that a model was called or that any previous failure has recovered.
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "last_success_at",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub last_success_at: Option<Option<String>>,
}

impl ExtractionObservation {
    #[allow(clippy::too_many_arguments)]
    pub fn new(status: ExtractionObservationStatus, since: String) -> ExtractionObservation {
        ExtractionObservation {
            status,
            since,
            last_failure: None,
            last_success_at: None,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum ExtractionObservationStatus {
    #[serde(rename = "unverified")]
    #[default]
    Unverified,
    #[serde(rename = "observed")]
    Observed,
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ExtractionStatus {
    /// Whether a local extraction model or custom pipeline is assembled. Configured does not verify credentials or connectivity. Unknown means execution is external and its configuration is not observed.
    #[serde(rename = "configuration")]
    pub configuration: Configuration,
    #[serde(rename = "background")]
    pub background: ExtractionBackground,
    #[serde(rename = "observation")]
    pub observation: ExtractionObservation,
}

impl ExtractionStatus {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        configuration: Configuration,
        background: ExtractionBackground,
        observation: ExtractionObservation,
    ) -> ExtractionStatus {
        ExtractionStatus {
            configuration,
            background,
            observation,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum Configuration {
    #[serde(rename = "configured")]
    #[default]
    Configured,
    #[serde(rename = "unconfigured")]
    Unconfigured,
    #[serde(rename = "unknown")]
    Unknown,
}

/// FeatureContract : Compatibility version for exactly the listed OpenAPI operation IDs. Adding operations or compatible semantics increments minor; removing, renaming or incompatibly changing a listed operation increments major.
#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct FeatureContract {
    #[serde(rename = "version")]
    pub version: ContractVersion,
    #[serde(rename = "operations")]
    pub operations: Vec<String>,
}

impl FeatureContract {
    /// Compatibility version for exactly the listed OpenAPI operation IDs. Adding operations or compatible semantics increments minor; removing, renaming or incompatibly changing a listed operation increments major.
    #[allow(clippy::too_many_arguments)]
    pub fn new(version: ContractVersion, operations: Vec<String>) -> FeatureContract {
        FeatureContract {
            version,
            operations,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct HealthResponse {
    #[serde(rename = "status")]
    pub status: String,
}

impl HealthResponse {
    #[allow(clippy::too_many_arguments)]
    pub fn new(status: String) -> HealthResponse {
        HealthResponse { status }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct MemoryMutationResponse {
    #[serde(rename = "changed")]
    pub changed: bool,
    #[serde(rename = "records")]
    pub records: Vec<AtomicMemoryRecord>,
}

impl MemoryMutationResponse {
    #[allow(clippy::too_many_arguments)]
    pub fn new(changed: bool, records: Vec<AtomicMemoryRecord>) -> MemoryMutationResponse {
        MemoryMutationResponse { changed, records }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum MemorySearchMode {
    #[serde(rename = "auto")]
    #[default]
    Auto,
    #[serde(rename = "fts")]
    Fts,
    #[serde(rename = "vector")]
    Vector,
    #[serde(rename = "hybrid")]
    Hybrid,
}

impl std::fmt::Display for MemorySearchMode {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Auto => write!(f, "auto"),
            Self::Fts => write!(f, "fts"),
            Self::Vector => write!(f, "vector"),
            Self::Hybrid => write!(f, "hybrid"),
        }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum PreparedContextSchema {
    #[serde(rename = "powercontext.prepared-context.v1")]
    #[default]
    PowercontextPreparedContextV1,
}

impl std::fmt::Display for PreparedContextSchema {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::PowercontextPreparedContextV1 => write!(f, "powercontext.prepared-context.v1"),
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct PromptCapability {
    #[serde(rename = "status")]
    pub status: PromptCapabilityStatus,
    #[ts(type = "Reason | null")]
    #[serde(rename = "reason", deserialize_with = "Option::deserialize")]
    pub reason: Option<Reason>,
    #[serde(rename = "definition_version")]
    pub definition_version: String,
    #[serde(rename = "builtin_version")]
    pub builtin_version: String,
    #[ts(type = "BuiltinProfile | null")]
    #[serde(rename = "builtin_profile", deserialize_with = "Option::deserialize")]
    pub builtin_profile: Option<BuiltinProfile>,
}

impl PromptCapability {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        status: PromptCapabilityStatus,
        reason: Option<Reason>,
        definition_version: String,
        builtin_version: String,
        builtin_profile: Option<BuiltinProfile>,
    ) -> PromptCapability {
        PromptCapability {
            status,
            reason,
            definition_version,
            builtin_version,
            builtin_profile,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum PromptCapabilityStatus {
    #[serde(rename = "supported")]
    #[default]
    Supported,
    #[serde(rename = "disabled")]
    Disabled,
    #[serde(rename = "unsupported")]
    Unsupported,
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum Reason {
    #[serde(rename = "operation_disabled")]
    #[default]
    OperationDisabled,
    #[serde(rename = "provider_not_configured")]
    ProviderNotConfigured,
    #[serde(rename = "injected_component")]
    InjectedComponent,
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum BuiltinProfile {
    #[serde(rename = "coding")]
    #[default]
    Coding,
    #[serde(rename = "conversation")]
    Conversation,
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ReadinessResponse {
    #[serde(rename = "status")]
    pub status: ReadinessStatus,
    #[serde(rename = "checks")]
    pub checks: std::collections::HashMap<String, String>,
}

impl ReadinessResponse {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        status: ReadinessStatus,
        checks: std::collections::HashMap<String, String>,
    ) -> ReadinessResponse {
        ReadinessResponse { status, checks }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum ReadinessStatus {
    #[serde(rename = "ready")]
    #[default]
    Ready,
    #[serde(rename = "degraded")]
    Degraded,
    #[serde(rename = "not_ready")]
    NotReady,
}

impl std::fmt::Display for ReadinessStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Ready => write!(f, "ready"),
            Self::Degraded => write!(f, "degraded"),
            Self::NotReady => write!(f, "not_ready"),
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct RememberMemoryRequest {
    #[serde(rename = "scope_id")]
    pub scope_id: String,
    #[serde(rename = "kind")]
    pub kind: String,
    /// Must not exceed 8192 UTF-8 bytes after normalization.
    #[serde(rename = "text")]
    pub text: String,
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "reason",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub reason: Option<Option<String>>,
    #[ts(optional)]
    #[ts(type = "number | null")]
    #[serde(
        rename = "expected_revision",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub expected_revision: Option<Option<i64>>,
    /// Optional supplied single-line summary. When omitted, the Server stores a whitespace-collapsed text prefix of at most 200 Unicode characters, including a trailing ellipsis when truncated. Explicit writes do not invoke a generation model.
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "description",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub description: Option<Option<String>>,
}

impl RememberMemoryRequest {
    #[allow(clippy::too_many_arguments)]
    pub fn new(scope_id: String, kind: String, text: String) -> RememberMemoryRequest {
        RememberMemoryRequest {
            scope_id,
            kind,
            text,
            reason: None,
            expected_revision: None,
            description: None,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ScopeDescriptor {
    #[serde(rename = "scope_id")]
    pub scope_id: String,
    #[serde(rename = "title")]
    pub title: String,
    #[serde(rename = "summary")]
    pub summary: String,
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "parent_scope_id",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub parent_scope_id: Option<Option<String>>,
    #[serde(rename = "context_references")]
    pub context_references: Vec<String>,
    #[serde(rename = "external_references")]
    pub external_references: Vec<ScopeExternalReference>,
    #[serde(rename = "version")]
    pub version: i64,
}

impl ScopeDescriptor {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        scope_id: String,
        title: String,
        summary: String,
        context_references: Vec<String>,
        external_references: Vec<ScopeExternalReference>,
        version: i64,
    ) -> ScopeDescriptor {
        ScopeDescriptor {
            scope_id,
            title,
            summary,
            parent_scope_id: None,
            context_references,
            external_references,
            version,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ScopeExternalReference {
    #[serde(rename = "kind")]
    pub kind: String,
    #[serde(rename = "value")]
    pub value: String,
}

impl ScopeExternalReference {
    #[allow(clippy::too_many_arguments)]
    pub fn new(kind: String, value: String) -> ScopeExternalReference {
        ScopeExternalReference { kind, value }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct ScopePage {
    #[serde(rename = "items")]
    pub items: Vec<ScopeDescriptor>,
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "next_cursor",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub next_cursor: Option<Option<String>>,
}

impl ScopePage {
    #[allow(clippy::too_many_arguments)]
    pub fn new(items: Vec<ScopeDescriptor>) -> ScopePage {
        ScopePage {
            items,
            next_cursor: None,
        }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum ScopeQueryField {
    #[serde(rename = "scope_id")]
    #[default]
    ScopeId,
    #[serde(rename = "title")]
    Title,
    #[serde(rename = "summary")]
    Summary,
    #[serde(rename = "external_reference_value")]
    ExternalReferenceValue,
    #[serde(rename = "binding_external_id")]
    BindingExternalId,
}

impl std::fmt::Display for ScopeQueryField {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::ScopeId => write!(f, "scope_id"),
            Self::Title => write!(f, "title"),
            Self::Summary => write!(f, "summary"),
            Self::ExternalReferenceValue => write!(f, "external_reference_value"),
            Self::BindingExternalId => write!(f, "binding_external_id"),
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct SearchMemoryRequest {
    #[ts(optional)]
    #[serde(rename = "tag_filter", skip_serializing_if = "Option::is_none")]
    pub tag_filter: Option<TagFilter>,
    #[serde(rename = "scope_id")]
    pub scope_id: String,
    #[serde(rename = "query")]
    pub query: String,
    #[ts(optional)]
    #[serde(rename = "limit", skip_serializing_if = "Option::is_none")]
    pub limit: Option<i64>,
    #[ts(optional)]
    #[serde(rename = "mode", skip_serializing_if = "Option::is_none")]
    pub mode: Option<MemorySearchMode>,
}

impl SearchMemoryRequest {
    #[allow(clippy::too_many_arguments)]
    pub fn new(scope_id: String, query: String) -> SearchMemoryRequest {
        SearchMemoryRequest {
            tag_filter: None,
            scope_id,
            query,
            limit: None,
            mode: None,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct SearchMemoryResponse {
    #[serde(rename = "mode")]
    pub mode: AtomicMemorySearchMode,
    #[serde(rename = "hits")]
    pub hits: Vec<AtomicMemorySearchHit>,
}

impl SearchMemoryResponse {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        mode: AtomicMemorySearchMode,
        hits: Vec<AtomicMemorySearchHit>,
    ) -> SearchMemoryResponse {
        SearchMemoryResponse { mode, hits }
    }
}

/// ServerInfo : Stable deployment identity and protocol compatibility metadata. Compatible clients must ignore unknown optional fields added by a future schema minor version. This contract intentionally excludes runtime capabilities, health, limits, inventory and authorization-principal identity.
#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
pub struct ServerInfo {
    /// Compatibility version for this response shape and field semantics.
    #[serde(rename = "schema_version")]
    pub schema_version: ContractVersion,
    #[serde(rename = "product")]
    pub product: Product,
    /// Opaque identity of the durable Server deployment. It is unrelated to Access deployment_id and remains stable across restarts, upgrades, backup restore and replicas sharing the same primary database.
    #[serde(rename = "server_id")]
    pub server_id: String,
    #[serde(rename = "package_version")]
    pub package_version: String,
    /// Major/minor projection of the OpenAPI info.version served by this package.
    #[serde(rename = "api_contract_version")]
    pub api_contract_version: ContractVersion,
    /// Stable feature groups keyed by contract name.
    #[serde(rename = "feature_contracts")]
    pub feature_contracts: std::collections::HashMap<String, FeatureContract>,
}

impl ServerInfo {
    /// Stable deployment identity and protocol compatibility metadata. Compatible clients must ignore unknown optional fields added by a future schema minor version. This contract intentionally excludes runtime capabilities, health, limits, inventory and authorization-principal identity.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        schema_version: ContractVersion,
        product: Product,
        server_id: String,
        package_version: String,
        api_contract_version: ContractVersion,
        feature_contracts: std::collections::HashMap<String, FeatureContract>,
    ) -> ServerInfo {
        ServerInfo {
            schema_version,
            product,
            server_id,
            package_version,
            api_contract_version,
            feature_contracts,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum Product {
    #[serde(rename = "powercontext")]
    #[default]
    Powercontext,
}

/// SourceLineRangeLocator : Inclusive one-based lines in adapter-supplied text, at most 200 lines. Never a revision redirect.
#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct SourceLineRangeLocator {
    #[serde(rename = "kind")]
    pub kind: SourceLineRangeLocatorKind,
    #[serde(rename = "start_line")]
    pub start_line: i64,
    #[serde(rename = "end_line")]
    pub end_line: i64,
    /// Optional adapter-supplied heading path, bounded in Unicode characters rather than UTF-8 bytes.
    #[ts(optional)]
    #[ts(type = "string | null")]
    #[serde(
        rename = "heading_path",
        default,
        with = "::serde_with::rust::double_option",
        skip_serializing_if = "Option::is_none"
    )]
    pub heading_path: Option<Option<String>>,
}

impl SourceLineRangeLocator {
    /// Inclusive one-based lines in adapter-supplied text, at most 200 lines. Never a revision redirect.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        kind: SourceLineRangeLocatorKind,
        start_line: i64,
        end_line: i64,
    ) -> SourceLineRangeLocator {
        SourceLineRangeLocator {
            kind,
            start_line,
            end_line,
            heading_path: None,
        }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum SourceLineRangeLocatorKind {
    #[serde(rename = "line_range")]
    #[default]
    LineRange,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(untagged)]
pub enum SourceLocator {
    #[serde(rename = "sequence")]
    Sequence(SourceSequenceLocator),
    #[serde(rename = "line_range")]
    LineRange(SourceLineRangeLocator),
}

impl Default for SourceLocator {
    fn default() -> Self {
        Self::Sequence(Default::default())
    }
}

/// SourceSequenceLocator : Position of this exact Source in its scope journal; zero is distinct from absence.
#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct SourceSequenceLocator {
    #[serde(rename = "kind")]
    pub kind: SourceSequenceLocatorKind,
    #[serde(rename = "sequence")]
    pub sequence: i64,
}

impl SourceSequenceLocator {
    /// Position of this exact Source in its scope journal; zero is distinct from absence.
    #[allow(clippy::too_many_arguments)]
    pub fn new(kind: SourceSequenceLocatorKind, sequence: i64) -> SourceSequenceLocator {
        SourceSequenceLocator { kind, sequence }
    }
}
#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum SourceSequenceLocatorKind {
    #[serde(rename = "sequence")]
    #[default]
    Sequence,
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct SourceTypeReference {
    /// Stable Source type, including dynamically registered Source names.
    #[serde(rename = "source_type")]
    pub source_type: String,
    /// Source identity as accepted at ingestion, including Unicode and interior spaces.
    #[serde(rename = "source_id")]
    pub source_id: String,
    #[ts(optional)]
    #[serde(rename = "locator", skip_serializing_if = "Option::is_none")]
    pub locator: Option<SourceLocator>,
}

impl SourceTypeReference {
    #[allow(clippy::too_many_arguments)]
    pub fn new(source_type: String, source_id: String) -> SourceTypeReference {
        SourceTypeReference {
            source_type,
            source_id,
            locator: None,
        }
    }
}

#[derive(Clone, Default, Debug, PartialEq, Serialize, Deserialize, TS)]
#[serde(deny_unknown_fields)]
pub struct TagFilter {
    #[serde(rename = "tags")]
    pub tags: Vec<String>,
    #[ts(optional)]
    #[serde(rename = "match", skip_serializing_if = "Option::is_none")]
    pub r#match: Option<TagMatch>,
}

impl TagFilter {
    #[allow(clippy::too_many_arguments)]
    pub fn new(tags: Vec<String>) -> TagFilter {
        TagFilter {
            tags,
            r#match: None,
        }
    }
}

#[derive(
    Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd, Hash, Serialize, Deserialize, TS, Default,
)]
pub enum TagMatch {
    #[serde(rename = "all")]
    #[default]
    All,
    #[serde(rename = "any")]
    Any,
}

impl std::fmt::Display for TagMatch {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::All => write!(f, "all"),
            Self::Any => write!(f, "any"),
        }
    }
}

#[rustfmt::skip]
pub fn declarations(config: &ts_rs::Config) -> Vec<String> {
    vec![
        <AccessAction as ts_rs::TS>::decl(config),
        <AccessControlMode as ts_rs::TS>::decl(config),
        <AccessMeResponse as ts_rs::TS>::decl(config),
        <AccessPrincipal as ts_rs::TS>::decl(config),
        <AccessProviderCapabilities as ts_rs::TS>::decl(config),
        <AccessResourceType as ts_rs::TS>::decl(config),
        <AccessRole as ts_rs::TS>::decl(config),
        <ArtifactDreamingOperationCapability as ts_rs::TS>::decl(config),
        <ArtifactFamilyAccessCapability as ts_rs::TS>::decl(config),
        <ArtifactReadFamily as ts_rs::TS>::decl(config),
        <ArtifactReference as ts_rs::TS>::decl(config),
        <ArtifactRevision as ts_rs::TS>::decl(config),
        <AtomicMemoryRecord as ts_rs::TS>::decl(config),
        <AtomicMemorySearchHit as ts_rs::TS>::decl(config),
        <AtomicMemorySearchMode as ts_rs::TS>::decl(config),
        <AtomicMemoryState as ts_rs::TS>::decl(config),
        <BuiltinProfile as ts_rs::TS>::decl(config),
        <Capabilities as ts_rs::TS>::decl(config),
        <Configuration as ts_rs::TS>::decl(config),
        <ContractVersion as ts_rs::TS>::decl(config),
        <DreamOperation as ts_rs::TS>::decl(config),
        <Effect as ts_rs::TS>::decl(config),
        <ExtractionBackground as ts_rs::TS>::decl(config),
        <ExtractionFailure as ts_rs::TS>::decl(config),
        <ExtractionObservation as ts_rs::TS>::decl(config),
        <ExtractionObservationStatus as ts_rs::TS>::decl(config),
        <ExtractionStatus as ts_rs::TS>::decl(config),
        <FeatureContract as ts_rs::TS>::decl(config),
        <HealthResponse as ts_rs::TS>::decl(config),
        <Location as ts_rs::TS>::decl(config),
        <MatchedBy as ts_rs::TS>::decl(config),
        <MemoryMutationResponse as ts_rs::TS>::decl(config),
        <MemorySearchMode as ts_rs::TS>::decl(config),
        <OutputKind as ts_rs::TS>::decl(config),
        <PreparedContextSchema as ts_rs::TS>::decl(config),
        <Product as ts_rs::TS>::decl(config),
        <PromptCapability as ts_rs::TS>::decl(config),
        <PromptCapabilityStatus as ts_rs::TS>::decl(config),
        <ReadinessResponse as ts_rs::TS>::decl(config),
        <ReadinessStatus as ts_rs::TS>::decl(config),
        <Reason as ts_rs::TS>::decl(config),
        <RememberMemoryRequest as ts_rs::TS>::decl(config),
        <Role as ts_rs::TS>::decl(config),
        <ScopeDescriptor as ts_rs::TS>::decl(config),
        <ScopeExternalReference as ts_rs::TS>::decl(config),
        <ScopePage as ts_rs::TS>::decl(config),
        <ScopeQueryField as ts_rs::TS>::decl(config),
        <SearchMemoryRequest as ts_rs::TS>::decl(config),
        <SearchMemoryResponse as ts_rs::TS>::decl(config),
        <ServerInfo as ts_rs::TS>::decl(config),
        <ShareUnit as ts_rs::TS>::decl(config),
        <SourceLineRangeLocator as ts_rs::TS>::decl(config),
        <SourceLineRangeLocatorKind as ts_rs::TS>::decl(config),
        <SourceLocator as ts_rs::TS>::decl(config),
        <SourceSequenceLocator as ts_rs::TS>::decl(config),
        <SourceSequenceLocatorKind as ts_rs::TS>::decl(config),
        <SourceTypeReference as ts_rs::TS>::decl(config),
        <Stage as ts_rs::TS>::decl(config),
        <State as ts_rs::TS>::decl(config),
        <TagFilter as ts_rs::TS>::decl(config),
        <TagMatch as ts_rs::TS>::decl(config),
        <Type as ts_rs::TS>::decl(config),
    ]
}
