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

//! Operation-specific adapters. No renderer-supplied route, method or header is accepted.
use super::{Endpoint, MAX_RESPONSE_BYTES, Transport, safe_network_error, wire::*};
use crate::{credentials::Secret, error::SafeError};
use reqwest::{
    Method,
    header::{AUTHORIZATION, HeaderValue},
};
use serde::{Deserialize, Serialize, de::DeserializeOwned};
use ts_rs::TS;

// This adapter retains the qualified pre-Atomic Memory wire contract.
pub const LEGACY_MEMORY_CONTRACT_SHA256: &str =
    "9af88b2b779c372a21ae9f398d0ca75b333f2e5da2afa3626df879911a442edd";

#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum MemoryReference {
    Citation { citation: MemoryCitation },
    Artifact { artifact: ArtifactReference },
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields)]
pub struct LegacyMemoryMutationResponse {
    pub memory: ArtifactReference,
    #[ts(optional = nullable)]
    pub entry: Option<MemoryEntry>,
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(untagged)]
pub enum MemoryMutationResult {
    Atomic(MemoryMutationResponse),
    Legacy(Box<LegacyMemoryMutationResponse>),
}
impl MemoryMutationResult {
    pub fn references(&self) -> Vec<MemoryReference> {
        match self {
            Self::Atomic(result) => result
                .records
                .iter()
                .map(|record| MemoryReference::Artifact {
                    artifact: record.artifact.clone(),
                })
                .collect(),
            Self::Legacy(result) => result
                .entry
                .iter()
                .map(|entry| MemoryReference::Citation {
                    citation: entry.citation.clone(),
                })
                .collect(),
        }
    }
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields)]
pub struct LegacySearchMemoryResponse {
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional = nullable)]
    pub memory: Option<ArtifactReference>,
    pub mode: Option<MemoryUsedSearchMode>,
    pub hits: Vec<SearchMemoryHit>,
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(untagged)]
pub enum MemorySearchResponse {
    Atomic(SearchMemoryResponse),
    Legacy(LegacySearchMemoryResponse),
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields)]
pub struct AtomicMemoryCreation {
    pub r#type: String,
    pub input_artifact_ids: Vec<String>,
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields)]
pub struct AtomicMemoryContent {
    pub schema: String,
    pub kind: String,
    pub text: String,
    pub creation: Option<AtomicMemoryCreation>,
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(deny_unknown_fields)]
pub struct AtomicMemoryRevision {
    pub scope_id: String,
    pub family: String,
    pub artifact_id: String,
    pub revision: i64,
    pub content: AtomicMemoryContent,
    pub sources: Vec<SourceTypeReference>,
    pub artifacts: Vec<ArtifactReference>,
    #[serde(default)]
    pub memory_citations: Vec<MemoryCitation>,
    pub content_digest: String,
}
#[derive(Clone, Debug, PartialEq, Deserialize, Serialize, TS)]
#[serde(untagged)]
pub enum MemoryEntryResult {
    Legacy(MemoryEntry),
    Atomic(AtomicMemoryRevision),
}

#[derive(Clone, Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ApiFailure {
    pub code: SafeError,
    pub request_id: Option<String>,
    /// Conservatively true once handed to the HTTP client, even if delivery is uncertain.
    pub dispatched: bool,
}
impl ApiFailure {
    pub fn before(code: SafeError) -> Self {
        Self {
            code,
            request_id: None,
            dispatched: false,
        }
    }
}
impl From<SafeError> for ApiFailure {
    fn from(code: SafeError) -> Self {
        Self::before(code)
    }
}

pub struct ServerApi {
    transport: Transport,
    endpoint: Endpoint,
    credential: Option<Secret>,
}
impl ServerApi {
    pub fn new(
        endpoint: Endpoint,
        ca_pem: Option<&[u8]>,
        credential: Option<Secret>,
    ) -> Result<Self, SafeError> {
        Ok(Self {
            transport: Transport::new(ca_pem)?,
            endpoint,
            credential,
        })
    }
    async fn execute<T: DeserializeOwned>(
        &self,
        operation: &str,
        path_parameters: &[(&str, &str)],
        query: &[(String, String)],
        body: Option<serde_json::Value>,
        allow_not_ready: bool,
    ) -> Result<T, ApiFailure> {
        let _permit = self
            .transport
            .slots
            .try_acquire()
            .map_err(|_| ApiFailure::before(SafeError::Busy))?;
        let manifest: serde_json::Value = serde_json::from_str(include_str!("operations.json"))
            .map_err(|_| ApiFailure::before(SafeError::InvalidResponse))?;
        let descriptor = &manifest["operations"][operation];
        let method = descriptor["method"]
            .as_str()
            .ok_or(SafeError::InvalidResponse)?;
        let method =
            Method::from_bytes(method.as_bytes()).map_err(|_| SafeError::InvalidResponse)?;
        let mut url = self.endpoint.operation_url(operation)?;
        if !path_parameters.is_empty() {
            // The fixed native operation owns parameter names; URL segments encode their values.
            let path = descriptor["path"]
                .as_str()
                .ok_or(SafeError::InvalidResponse)?;
            let segments = path
                .trim_start_matches('/')
                .split('/')
                .map(|segment| {
                    if segment.starts_with('{') && segment.ends_with('}') {
                        let name = &segment[1..segment.len() - 1];
                        path_parameters
                            .iter()
                            .find(|(key, _)| *key == name)
                            .map(|(_, value)| *value)
                            .ok_or(SafeError::InvalidInput)
                    } else {
                        Ok(segment)
                    }
                })
                .collect::<Result<Vec<_>, _>>()?;
            url = self.endpoint.0.clone();
            url.path_segments_mut()
                .map_err(|_| SafeError::InvalidEndpoint)?
                .pop_if_empty()
                .extend(segments);
        }
        if !query.is_empty() {
            url.query_pairs_mut()
                .extend_pairs(query.iter().map(|(k, v)| (k.as_str(), v.as_str())));
        }
        let mut request = self.transport.client.request(method, url);
        if let Some(secret) = &self.credential {
            let mut value = HeaderValue::from_str(&format!("Bearer {}", secret.expose()))
                .map_err(|_| SafeError::InvalidCredential)?;
            value.set_sensitive(true);
            request = request.header(AUTHORIZATION, value);
        }
        if let Some(body) = body {
            request = request.json(&body);
        }
        let request = request.build().map_err(|_| SafeError::InvalidInput)?;
        let failure = |code, request_id| ApiFailure {
            code,
            request_id,
            dispatched: true,
        };
        let mut response = self
            .transport
            .client
            .execute(request)
            .await
            .map_err(|e| failure(safe_network_error(e), None))?;
        let request_id = response
            .headers()
            .get("X-PowerContext-Request-ID")
            .and_then(|v| v.to_str().ok())
            .filter(|v| {
                !v.is_empty()
                    && v.len() <= 128
                    && v.bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
            })
            .map(str::to_owned);
        let status = response.status().as_u16();
        if status != 200 && !(allow_not_ready && status == 503) {
            let code = match status {
                300..=399 => SafeError::Redirect,
                401 => SafeError::Unauthorized,
                403 => SafeError::Forbidden,
                404 => SafeError::NotFound,
                409 => SafeError::Conflict,
                410 => SafeError::CursorExpired,
                400 | 422 => SafeError::InvalidInput,
                503 => server_error_code(&mut response).await,
                500..=599 => SafeError::Server,
                _ => SafeError::InvalidResponse,
            };
            return Err(failure(code, request_id));
        }
        if response
            .content_length()
            .is_some_and(|n| n > MAX_RESPONSE_BYTES as u64)
        {
            return Err(failure(SafeError::ResponseTooLarge, request_id));
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|e| failure(safe_network_error(e), request_id.clone()))?
        {
            if bytes.len() + chunk.len() > MAX_RESPONSE_BYTES {
                return Err(failure(SafeError::ResponseTooLarge, request_id));
            }
            bytes.extend_from_slice(&chunk);
        }
        serde_json::from_slice(&bytes).map_err(|_| failure(SafeError::InvalidResponse, request_id))
    }
    pub async fn live(&self) -> Result<HealthResponse, ApiFailure> {
        let result: HealthResponse = self.execute("get_liveness", &[], &[], None, false).await?;
        if result.status != "ok" {
            return Err(SafeError::InvalidResponse.into());
        }
        Ok(result)
    }
    pub async fn readiness(&self) -> Result<ReadinessResponse, ApiFailure> {
        self.execute("get_readiness", &[], &[], None, true).await
    }
    pub async fn principal(&self) -> Result<AccessMeResponse, ApiFailure> {
        let result: AccessMeResponse = self
            .execute("get_access_principal", &[], &[], None, false)
            .await?;
        if result.principal.id.is_empty()
            || result.principal.id.chars().count() > 255
            || !matches!(result.principal.r#type.as_str(), "user" | "service")
        {
            return Err(SafeError::InvalidResponse.into());
        }
        Ok(result)
    }
    pub async fn capabilities(&self) -> Result<Capabilities, ApiFailure> {
        self.execute("get_capabilities", &[], &[], None, false)
            .await
    }
    pub async fn scopes(
        &self,
        search: &str,
        cursor: Option<&str>,
    ) -> Result<ScopePage, ApiFailure> {
        if search.chars().count() > 256 || cursor.is_some_and(|v| v.is_empty() || v.len() > 4096) {
            return Err(SafeError::InvalidInput.into());
        }
        let mut query = vec![("limit".into(), "50".into())];
        if !search.is_empty() {
            query.extend([
                ("query".into(), search.into()),
                ("query_field".into(), "title".into()),
            ]);
        }
        if let Some(cursor) = cursor {
            query.push(("cursor".into(), cursor.into()));
        }
        let result: ScopePage = self
            .execute("list_scopes", &[], &query, None, false)
            .await?;
        if result.items.len() > 50
            || result
                .next_cursor
                .as_ref()
                .is_some_and(|c| c.is_empty() || c.len() > 4096)
        {
            return Err(SafeError::InvalidResponse.into());
        }
        for scope in &result.items {
            validate_scope(&scope.scope_id).map_err(|_| SafeError::InvalidResponse)?;
        }
        Ok(result)
    }
    pub async fn scope(&self, id: &str) -> Result<ScopeDescriptor, ApiFailure> {
        validate_scope(id)?;
        let result: ScopeDescriptor = self
            .execute("get_scope", &[("scope_id", id)], &[], None, false)
            .await?;
        if result.scope_id != id {
            return Err(SafeError::InvalidResponse.into());
        }
        Ok(result)
    }
    pub async fn default_scope(&self) -> Result<ScopeDescriptor, ApiFailure> {
        self.execute("get_default_scope", &[], &[], None, false)
            .await
    }
    pub async fn remember(
        &self,
        scope: &str,
        text: &str,
    ) -> Result<MemoryMutationResult, ApiFailure> {
        validate_scope(scope)?;
        validate_text(text)?;
        let result: MemoryMutationResult = self
            .execute(
                "remember_memory",
                &[],
                &[],
                Some(serde_json::json!({"scope_id":scope,"kind":"note","text":text})),
                false,
            )
            .await?;
        let valid = match &result {
            MemoryMutationResult::Atomic(value) => value.records.iter().all(valid_atomic_record),
            MemoryMutationResult::Legacy(value) => {
                valid_reference(&value.memory)
                    && value.memory.family == "memory"
                    && value.entry.as_ref().is_none_or(|entry| {
                        valid_entry(entry) && entry.citation.memory_ref == value.memory
                    })
            }
        };
        if !valid {
            return Err(invalid_received());
        }
        Ok(result)
    }

    pub async fn search(
        &self,
        scope: &str,
        query: &str,
    ) -> Result<MemorySearchResponse, ApiFailure> {
        validate_scope(scope)?;
        validate_text(query)?;
        let result: MemorySearchResponse = self
            .execute(
                "search_memory",
                &[],
                &[],
                Some(serde_json::json!({"scope_id":scope,"query":query,"mode":"fts","limit":10})),
                false,
            )
            .await?;
        let valid = match &result {
            MemorySearchResponse::Atomic(value) => {
                value.hits.len() <= 10
                    && value.mode == AtomicMemorySearchMode::Text
                    && value
                        .hits
                        .iter()
                        .all(|hit| valid_atomic_record(&hit.memory))
            }
            MemorySearchResponse::Legacy(value) => {
                value.hits.len() <= 10
                    && value
                        .mode
                        .as_ref()
                        .is_none_or(|mode| *mode == MemoryUsedSearchMode::Fts)
                    && value
                        .hits
                        .iter()
                        .all(|hit| validate_citation(&hit.citation).is_ok())
            }
        };
        if !valid {
            return Err(SafeError::InvalidResponse.into());
        }
        Ok(result)
    }
    pub async fn entry(
        &self,
        scope: &str,
        reference: &MemoryReference,
    ) -> Result<MemoryEntryResult, ApiFailure> {
        validate_scope(scope)?;
        match reference {
            MemoryReference::Citation { citation } => {
                validate_citation(citation)?;
                let result: GetMemoryEntryResponse = self
                    .execute(
                        "get_memory_entry",
                        &[],
                        &[],
                        Some(serde_json::json!({"scope_id":scope,"citation":citation})),
                        false,
                    )
                    .await?;
                let GetMemoryEntryResponse::MemoryEntry(entry) = result else {
                    return Err(invalid_received());
                };
                if entry.citation != *citation || !valid_entry(&entry) {
                    return Err(invalid_received());
                }
                Ok(MemoryEntryResult::Legacy(entry))
            }
            MemoryReference::Artifact { artifact } => {
                if artifact.family != "atomic-memory" || !valid_reference(artifact) {
                    return Err(SafeError::InvalidInput.into());
                }
                let revision = artifact.revision.to_string();
                let result: AtomicMemoryRevision = self
                    .execute(
                        "get_artifact_revision",
                        &[
                            ("scope_id", scope),
                            ("family", &artifact.family),
                            ("artifact_id", &artifact.artifact_id),
                            ("revision", &revision),
                        ],
                        &[],
                        None,
                        false,
                    )
                    .await?;
                if result.scope_id != scope
                    || result.family != artifact.family
                    || result.artifact_id != artifact.artifact_id
                    || result.revision != artifact.revision
                    || result.content.schema != "powercontext.atomic-memory.v1"
                    || result.content.kind.trim().is_empty()
                    || result.content.text.trim().is_empty()
                    || !result.artifacts.iter().all(valid_reference)
                    || !result
                        .memory_citations
                        .iter()
                        .all(|citation| validate_citation(citation).is_ok())
                {
                    return Err(invalid_received());
                }
                Ok(MemoryEntryResult::Atomic(result))
            }
        }
    }
}
pub fn validate_scope(id: &str) -> Result<(), SafeError> {
    if id.trim().is_empty() || id.chars().count() > 256 || matches!(id, "." | "..") {
        return Err(SafeError::InvalidInput);
    }
    Ok(())
}
pub fn validate_text(text: &str) -> Result<(), SafeError> {
    // Conservative raw UTF-8 budget; do not alter or truncate user content before the Server normalizes it.
    if text.trim().is_empty() || text.len() > 8192 {
        return Err(SafeError::InvalidInput);
    }
    Ok(())
}
fn validate_citation(c: &MemoryCitation) -> Result<(), SafeError> {
    if c.memory_ref.family != "memory"
        || !valid_reference(&c.memory_ref)
        || [&c.memory_ref.artifact_id, &c.entry_id, &c.entry_version_id]
            .iter()
            .any(|v| v.is_empty() || v.len() > 128 || !v.bytes().all(|b| b.is_ascii_graphic()))
    {
        return Err(SafeError::InvalidInput);
    }
    Ok(())
}

fn valid_reference(reference: &ArtifactReference) -> bool {
    (1..=9_007_199_254_740_991).contains(&reference.revision)
        && !reference.family.is_empty()
        && reference.family.len() <= 128
        && !reference.artifact_id.is_empty()
        && reference.artifact_id.len() <= 128
        && reference.artifact_id.bytes().all(|b| b.is_ascii_graphic())
}
fn valid_entry(entry: &MemoryEntry) -> bool {
    validate_citation(&entry.citation).is_ok()
        && (1..=9_007_199_254_740_991).contains(&entry.version)
        && entry.artifact_refs.iter().all(valid_reference)
}
fn valid_atomic_record(record: &AtomicMemoryRecord) -> bool {
    record.artifact.family == "atomic-memory"
        && valid_reference(&record.artifact)
        && !record.kind.trim().is_empty()
        && !record.text.trim().is_empty()
        && (0..=9_007_199_254_740_991).contains(&record.state_version)
        && (record.state == AtomicMemoryState::Merged) == record.merged_into_id.is_some()
}
fn invalid_received() -> ApiFailure {
    ApiFailure {
        code: SafeError::InvalidResponse,
        request_id: None,
        dispatched: true,
    }
}

async fn server_error_code(response: &mut reqwest::Response) -> SafeError {
    // Project only allowlisted machine codes, never error messages or details.
    let mut bytes = Vec::new();
    while let Ok(Some(chunk)) = response.chunk().await {
        if bytes.len() + chunk.len() > 8192 {
            return SafeError::Server;
        }
        bytes.extend_from_slice(&chunk);
    }
    match serde_json::from_slice::<serde_json::Value>(&bytes)
        .ok()
        .as_ref()
        .and_then(|v| v.get("error"))
        .and_then(|v| v.get("code"))
        .and_then(|v| v.as_str())
    {
        Some("authentication_unavailable") => SafeError::AuthenticationUnavailable,
        Some("runtime_not_ready") => SafeError::RuntimeNotReady,
        _ => SafeError::Server,
    }
}
