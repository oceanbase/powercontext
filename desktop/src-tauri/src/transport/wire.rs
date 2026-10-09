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
#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum AccessAction {
    #[serde(rename = "server.observe")]
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

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum AccessControlMode {
    #[serde(rename = "disabled")]
    Disabled,
    #[serde(rename = "enforced")]
    Enforced,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct AccessMeResponse {
    pub r#principal: AccessPrincipal,
    pub r#mode: AccessControlMode,
    pub r#resource_kinds: Vec<AccessResourceType>,
    pub r#provider_capabilities: AccessProviderCapabilities,
    pub r#artifact_families: Vec<ArtifactFamilyAccessCapability>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct AccessPrincipal {
    pub r#type: String,
    pub r#id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#description: Option<String>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct AccessProviderCapabilities {
    pub r#safe_resource_filtering: bool,
    pub r#multi_requirement_check: bool,
    pub r#relationship_management: bool,
    pub r#group_subjects: bool,
    pub r#multi_principal: bool,
    pub r#max_direct_resource_keys: i64,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum AccessResourceType {
    #[serde(rename = "server")]
    Server,
    #[serde(rename = "scope")]
    Scope,
    #[serde(rename = "artifact")]
    Artifact,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum AccessRole {
    #[serde(rename = "handoff.viewer")]
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

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct ArtifactFamilyAccessCapability {
    pub r#family: String,
    pub r#enabled: bool,
    pub r#share_unit: String,
    pub r#actions: Vec<AccessAction>,
    pub r#grantable_roles: Vec<AccessRole>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct ArtifactReference {
    pub r#family: String,
    pub r#artifact_id: String,
    pub r#revision: i64,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct Capabilities {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#prompts: Option<std::collections::BTreeMap<String, PromptCapability>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#artifact_dreaming: Option<bool>,
    pub r#source_types: Vec<String>,
    pub r#artifact_families: Vec<String>,
    pub r#memory_extraction: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#experience_generation: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#managed_skill_generation: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#external_skill_registry: Option<bool>,
    pub r#handoff_generation: bool,
    pub r#search_modes: Vec<MemorySearchMode>,
    pub r#context_versions: Vec<PreparedContextSchema>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct GetMemoryEntryRequest {
    pub r#scope_id: String,
    pub r#citation: MemoryCitation,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct HealthResponse {
    pub r#status: String,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct MemoryCitation {
    pub r#memory_ref: ArtifactReference,
    pub r#entry_id: String,
    pub r#entry_version_id: String,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct MemoryEntry {
    pub r#citation: MemoryCitation,
    pub r#version: i64,
    pub r#kind: String,
    pub r#text: String,
    pub r#state: MemoryEntryState,
    pub r#source_refs: Vec<SourceReference>,
    pub r#artifact_refs: Vec<ArtifactReference>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum MemoryEntryState {
    #[serde(rename = "active")]
    Active,
    #[serde(rename = "inactive")]
    Inactive,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum MemoryMatchedBy {
    #[serde(rename = "fts")]
    Fts,
    #[serde(rename = "vector")]
    Vector,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct MemoryMutationResponse {
    pub r#memory: ArtifactReference,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#entry: Option<MemoryEntry>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum MemorySearchMode {
    #[serde(rename = "auto")]
    Auto,
    #[serde(rename = "fts")]
    Fts,
    #[serde(rename = "vector")]
    Vector,
    #[serde(rename = "hybrid")]
    Hybrid,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum MemoryUsedSearchMode {
    #[serde(rename = "fts")]
    Fts,
    #[serde(rename = "vector")]
    Vector,
    #[serde(rename = "hybrid")]
    Hybrid,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum PreparedContextSchema {
    #[serde(rename = "powercontext.prepared-context.v1")]
    PowercontextPreparedContextV1,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct PromptCapability {
    pub r#status: String,
    pub r#reason: Option<String>,
    pub r#definition_version: String,
    pub r#builtin_version: String,
    pub r#builtin_profile: Option<String>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct ReadinessResponse {
    pub r#status: ReadinessStatus,
    pub r#checks: std::collections::BTreeMap<String, String>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum ReadinessStatus {
    #[serde(rename = "ready")]
    Ready,
    #[serde(rename = "degraded")]
    Degraded,
    #[serde(rename = "not_ready")]
    NotReady,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct RememberMemoryRequest {
    pub r#scope_id: String,
    pub r#kind: String,
    pub r#text: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#expected_revision: Option<i64>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct ScopeDescriptor {
    pub r#scope_id: String,
    pub r#title: String,
    pub r#summary: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#parent_scope_id: Option<String>,
    pub r#context_references: Vec<String>,
    pub r#external_references: Vec<ScopeExternalReference>,
    pub r#version: i64,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct ScopeExternalReference {
    pub r#kind: String,
    pub r#value: String,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct ScopePage {
    pub r#items: Vec<ScopeDescriptor>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#next_cursor: Option<String>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum ScopeQueryField {
    #[serde(rename = "scope_id")]
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

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct SearchMemoryHit {
    pub r#citation: MemoryCitation,
    pub r#text: String,
    pub r#score: f64,
    pub r#matched_by: Vec<MemoryMatchedBy>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct SearchMemoryRequest {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#tag_filter: Option<TagFilter>,
    pub r#scope_id: String,
    pub r#query: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#limit: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#mode: Option<MemorySearchMode>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct SearchMemoryResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#memory: Option<ArtifactReference>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#mode: Option<MemoryUsedSearchMode>,
    pub r#hits: Vec<SearchMemoryHit>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct SourceReference {
    pub r#name: String,
    pub r#source_id: String,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
#[serde(deny_unknown_fields)]
pub struct TagFilter {
    pub r#tags: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub r#match: Option<TagMatch>,
}

#[derive(Clone, Debug, PartialEq, serde::Deserialize, serde::Serialize, ts_rs::TS)]
pub enum TagMatch {
    #[serde(rename = "all")]
    All,
    #[serde(rename = "any")]
    Any,
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
        <ArtifactFamilyAccessCapability as ts_rs::TS>::decl(config),
        <ArtifactReference as ts_rs::TS>::decl(config),
        <Capabilities as ts_rs::TS>::decl(config),
        <GetMemoryEntryRequest as ts_rs::TS>::decl(config),
        <HealthResponse as ts_rs::TS>::decl(config),
        <MemoryCitation as ts_rs::TS>::decl(config),
        <MemoryEntry as ts_rs::TS>::decl(config),
        <MemoryEntryState as ts_rs::TS>::decl(config),
        <MemoryMatchedBy as ts_rs::TS>::decl(config),
        <MemoryMutationResponse as ts_rs::TS>::decl(config),
        <MemorySearchMode as ts_rs::TS>::decl(config),
        <MemoryUsedSearchMode as ts_rs::TS>::decl(config),
        <PreparedContextSchema as ts_rs::TS>::decl(config),
        <PromptCapability as ts_rs::TS>::decl(config),
        <ReadinessResponse as ts_rs::TS>::decl(config),
        <ReadinessStatus as ts_rs::TS>::decl(config),
        <RememberMemoryRequest as ts_rs::TS>::decl(config),
        <ScopeDescriptor as ts_rs::TS>::decl(config),
        <ScopeExternalReference as ts_rs::TS>::decl(config),
        <ScopePage as ts_rs::TS>::decl(config),
        <ScopeQueryField as ts_rs::TS>::decl(config),
        <SearchMemoryHit as ts_rs::TS>::decl(config),
        <SearchMemoryRequest as ts_rs::TS>::decl(config),
        <SearchMemoryResponse as ts_rs::TS>::decl(config),
        <SourceReference as ts_rs::TS>::decl(config),
        <TagFilter as ts_rs::TS>::decl(config),
        <TagMatch as ts_rs::TS>::decl(config),
    ]
}
