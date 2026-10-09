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

// Generated from Rust IPC types. Do not edit.
export type SafeError = "unauthorized_window" | "invalid_endpoint" | "insecure_transport" | "invalid_certificate" | "credential_unavailable" | "credential_missing" | "invalid_credential" | "timeout" | "tls" | "network" | "redirect" | "unauthorized" | "forbidden" | "server" | "invalid_response" | "response_too_large" | "busy" | "invalid_input" | "not_found" | "cursor_expired" | "conflict" | "authentication_unavailable" | "runtime_not_ready" | "stale_context" | "compatibility_unverified" | "storage" | "profile_corrupt" | "duplicate_name" | "not_connected" | "scope_required";
export type FoundationInfo = { version: string, phase: string, credentialBackend: string, serverConnected: boolean, };
export type StorageChoice = "persistent" | "session_only";
export type CredentialWriteRequest = { secret: string, storage: StorageChoice, };
export type CredentialWriteReceipt = { storage: StorageChoice, };
export type DiagnosticKind = "service" | "integrations";
export type DiagnosticItem = { field: string, status: string, };
export type HostDiagnostic = { host: string, presence: string, checks: Array<DiagnosticItem>, };
export type DiagnosticReport = { checkedAt: number, exitCode: number, items: Array<DiagnosticItem>, hosts: Array<HostDiagnostic>, };
export type Authentication = "unauthenticated_loopback" | "bearer";
export type ProfileView = { id: string, revision: number, name: string, endpoint: string, authentication: Authentication, caPem: string | null, compatibility: string | null, credentialState: CredentialState, };
export type ProfileInput = { id: string | null, revision: number | null, name: string, endpoint: string, authentication: Authentication, caPem: string | null, compatibility: string | null, keepCredential: boolean, credential: CredentialWriteRequest | null, };
export type CredentialState = "not_required" | "stored" | "session_only" | "missing";
export type Fact<T> = { value: T | null, error: ApiFailure | null, };
export type CheckReport = { connectionId: string, revision: number, checkedAt: number, liveness: Fact<HealthResponse>, readiness: Fact<ReadinessResponse>, identity: Fact<AccessMeResponse>, capabilities: Fact<Capabilities>, compatibilityVerified: boolean, anonymousAccess: boolean, supportedOperations: Array<string>, };
export type CompatibilityProfile = { id: string, serverCommit: string, contractSha256: string, artifactSha256: string, operations: Array<string>, evidence: string, };
export type ActiveView = { connectionId: string, generation: number, report: CheckReport, scope: ScopeDescriptor | null, };
export type MemoryContext = { connectionId: string, endpoint: string, principal: AccessPrincipal | null, generation: number, scopeId: string, };
export type WriteStatus = "pending" | "succeeded" | "failed" | "unknown";
export type WriteRecord = { operationId: string, context: MemoryContext, status: WriteStatus, citation: MemoryCitation | null, error: ApiFailure | null, };
export type WriteOutcome = { record: WriteRecord, result: MemoryMutationResponse | null, };
export type DesktopState = { generation: number, profiles: Array<ProfileView>, reports: Array<CheckReport>, active: ActiveView | null, compatibilityProfiles: Array<CompatibilityProfile>, pendingCredentialCleanup: number, lastWrite: WriteRecord | null, };
export type ApiFailure = { code: SafeError, requestId: string | null,
/**
 * Conservatively true once handed to the HTTP client, even if delivery is uncertain.
 */
dispatched: boolean, };
export type AccessAction = "server.observe" | "server.admin" | "scope.read" | "scope.contribute" | "scope.review" | "scope.delegate" | "scope.admin" | "artifact.read" | "artifact.write" | "artifact.share" | "handoff.evidence.inspect" | "handoff.acknowledge" | "prompt.use";
export type AccessControlMode = "disabled" | "enforced";
export type AccessMeResponse = { principal: AccessPrincipal, mode: AccessControlMode, resource_kinds: Array<AccessResourceType>, provider_capabilities: AccessProviderCapabilities, artifact_families: Array<ArtifactFamilyAccessCapability>, };
export type AccessPrincipal = { type: string, id: string, description?: string | null, };
export type AccessProviderCapabilities = { safe_resource_filtering: boolean, multi_requirement_check: boolean, relationship_management: boolean, group_subjects: boolean, multi_principal: boolean, max_direct_resource_keys: number, };
export type AccessResourceType = "server" | "scope" | "artifact";
export type AccessRole = "handoff.viewer" | "handoff.receiver" | "artifact.viewer" | "prompt.user" | "artifact.owner" | "scope.viewer" | "scope.contributor" | "scope.reviewer" | "scope.delegator" | "scope.admin" | "server.observer" | "server.admin";
export type ArtifactFamilyAccessCapability = { family: string, enabled: boolean, share_unit: string, actions: Array<AccessAction>, grantable_roles: Array<AccessRole>, };
export type ArtifactReference = { family: string, artifact_id: string, revision: number, };
export type Capabilities = { prompts?: { [key in string]: PromptCapability } | null, artifact_dreaming?: boolean | null, source_types: Array<string>, artifact_families: Array<string>, memory_extraction: boolean, experience_generation?: boolean | null, managed_skill_generation?: boolean | null, external_skill_registry?: boolean | null, handoff_generation: boolean, search_modes: Array<MemorySearchMode>, context_versions: Array<PreparedContextSchema>, };
export type GetMemoryEntryRequest = { scope_id: string, citation: MemoryCitation, };
export type HealthResponse = { status: string, };
export type MemoryCitation = { memory_ref: ArtifactReference, entry_id: string, entry_version_id: string, };
export type MemoryEntry = { citation: MemoryCitation, version: number, kind: string, text: string, state: MemoryEntryState, source_refs: Array<SourceReference>, artifact_refs: Array<ArtifactReference>, };
export type MemoryEntryState = "active" | "inactive";
export type MemoryMatchedBy = "fts" | "vector";
export type MemoryMutationResponse = { memory: ArtifactReference, entry?: MemoryEntry | null, };
export type MemorySearchMode = "auto" | "fts" | "vector" | "hybrid";
export type MemoryUsedSearchMode = "fts" | "vector" | "hybrid";
export type PreparedContextSchema = "powercontext.prepared-context.v1";
export type PromptCapability = { status: string, reason: string | null, definition_version: string, builtin_version: string, builtin_profile: string | null, };
export type ReadinessResponse = { status: ReadinessStatus, checks: { [key in string]: string }, };
export type ReadinessStatus = "ready" | "degraded" | "not_ready";
export type RememberMemoryRequest = { scope_id: string, kind: string, text: string, reason?: string | null, expected_revision?: number | null, };
export type ScopeDescriptor = { scope_id: string, title: string, summary: string, parent_scope_id?: string | null, context_references: Array<string>, external_references: Array<ScopeExternalReference>, version: number, };
export type ScopeExternalReference = { kind: string, value: string, };
export type ScopePage = { items: Array<ScopeDescriptor>, next_cursor?: string | null, };
export type ScopeQueryField = "scope_id" | "title" | "summary" | "external_reference_value" | "binding_external_id";
export type SearchMemoryHit = { citation: MemoryCitation, text: string, score: number, matched_by: Array<MemoryMatchedBy>, };
export type SearchMemoryRequest = { tag_filter?: TagFilter | null, scope_id: string, query: string, limit?: number | null, mode?: MemorySearchMode | null, };
export type SearchMemoryResponse = { memory?: ArtifactReference | null, mode?: MemoryUsedSearchMode | null, hits: Array<SearchMemoryHit>, };
export type SourceReference = { name: string, source_id: string, };
export type TagFilter = { tags: Array<string>, match?: TagMatch | null, };
export type TagMatch = "all" | "any";
