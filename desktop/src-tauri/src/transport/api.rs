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
use serde::{Serialize, de::DeserializeOwned};
use ts_rs::TS;

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

#[derive(Clone, Serialize, TS)]
pub struct MemorySaveResponse {
    pub changed: bool,
    pub records: Vec<AtomicMemoryRecord>,
}
#[derive(Clone, Serialize, TS)]
pub struct MemorySearchHit {
    pub artifact: ArtifactReference,
    pub text: String,
    pub score: f64,
    pub matched_by: Vec<String>,
}
#[derive(Clone, Serialize, TS)]
pub struct MemorySearchResponse {
    pub hits: Vec<MemorySearchHit>,
}
#[derive(Clone, Debug, Serialize, TS)]
pub struct AtomicMemoryDetail {
    pub artifact: ArtifactReference,
    pub kind: String,
    pub text: String,
    pub source_refs: Vec<SourceTypeReference>,
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
        scope: Option<&str>,
        query: &[(String, String)],
        body: Option<serde_json::Value>,
        allow_not_ready: bool,
    ) -> Result<T, ApiFailure> {
        self.execute_path(operation, scope, query, body, allow_not_ready, None)
            .await
    }
    async fn execute_path<T: DeserializeOwned>(
        &self,
        operation: &str,
        scope: Option<&str>,
        query: &[(String, String)],
        body: Option<serde_json::Value>,
        allow_not_ready: bool,
        artifact: Option<&ArtifactReference>,
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
        if let Some(artifact) = artifact {
            if operation != "get_artifact_revision"
                || !descriptor["path"].as_str().is_some_and(|p| {
                    p.ends_with(
                        "/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}",
                    )
                })
            {
                return Err(SafeError::InvalidResponse.into());
            }
            let revision = artifact.revision.to_string();
            let mut segments = url
                .path_segments_mut()
                .map_err(|_| SafeError::InvalidEndpoint)?;
            for _ in 0..7 {
                segments.pop();
            }
            segments.extend([
                "scopes",
                scope.ok_or(SafeError::ScopeRequired)?,
                "artifacts",
                "atomic-memory",
                &artifact.artifact_id,
                "revisions",
                &revision,
            ]);
        } else if let Some(id) = scope {
            validate_scope(id)?;
            // Replace the generated placeholder using URL path-segment encoding, never string interpolation.
            if !descriptor["path"]
                .as_str()
                .is_some_and(|p| p.ends_with("/{scope_id}"))
            {
                return Err(SafeError::InvalidResponse.into());
            }
            url.path_segments_mut()
                .map_err(|_| SafeError::InvalidEndpoint)?
                .pop()
                .push(id);
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
        let result: HealthResponse = self.execute("get_liveness", None, &[], None, false).await?;
        if result.status != "ok" {
            return Err(SafeError::InvalidResponse.into());
        }
        Ok(result)
    }
    pub async fn readiness(&self) -> Result<ReadinessResponse, ApiFailure> {
        self.execute("get_readiness", None, &[], None, true).await
    }
    pub async fn principal(&self) -> Result<AccessMeResponse, ApiFailure> {
        let result: AccessMeResponse = self
            .execute("get_access_principal", None, &[], None, false)
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
        self.execute("get_capabilities", None, &[], None, false)
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
            .execute("list_scopes", None, &query, None, false)
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
        let result: ScopeDescriptor = self
            .execute("get_scope", Some(id), &[], None, false)
            .await?;
        if result.scope_id != id {
            return Err(SafeError::InvalidResponse.into());
        }
        Ok(result)
    }
    pub async fn default_scope(&self) -> Result<ScopeDescriptor, ApiFailure> {
        self.execute("get_default_scope", None, &[], None, false)
            .await
    }
    pub async fn remember(
        &self,
        scope: &str,
        text: &str,
    ) -> Result<MemorySaveResponse, ApiFailure> {
        validate_scope(scope)?;
        validate_text(text)?;
        let result: MemoryMutationResponse = self
            .execute(
                "remember_memory",
                None,
                &[],
                Some(serde_json::json!({"scope_id":scope,"kind":"note","text":text})),
                false,
            )
            .await?;
        if result
            .records
            .iter()
            .any(|record| !valid_atomic_record(record))
        {
            return Err(invalid_received());
        }
        Ok(MemorySaveResponse {
            changed: result.changed,
            records: result.records,
        })
    }

    pub async fn search(
        &self,
        scope: &str,
        query: &str,
    ) -> Result<MemorySearchResponse, ApiFailure> {
        validate_scope(scope)?;
        validate_text(query)?;
        let result: SearchMemoryResponse = self
            .execute(
                "search_memory",
                None,
                &[],
                Some(serde_json::json!({"scope_id":scope,"query":query,"mode":"fts","limit":10})),
                false,
            )
            .await?;
        if result.mode != AtomicMemorySearchMode::Text
            || result.hits.len() > 10
            || result.hits.iter().any(|hit| {
                !valid_atomic_record(&hit.memory)
                    || hit.memory.state != AtomicMemoryState::Active
                    || !hit.score.is_finite()
                    || hit
                        .matched_by
                        .iter()
                        .any(|channel| !matches!(channel.as_str(), "text" | "vector"))
            })
        {
            return Err(SafeError::InvalidResponse.into());
        }
        let hits = result
            .hits
            .into_iter()
            .map(|hit| MemorySearchHit {
                artifact: hit.memory.artifact,
                text: hit.memory.text,
                score: hit.score,
                matched_by: hit.matched_by,
            })
            .collect();
        Ok(MemorySearchResponse { hits })
    }
    pub async fn atomic_entry(
        &self,
        scope: &str,
        artifact: &ArtifactReference,
    ) -> Result<AtomicMemoryDetail, ApiFailure> {
        validate_scope(scope)?;
        if artifact.family != "atomic-memory" || !valid_reference(artifact) {
            return Err(SafeError::InvalidInput.into());
        }
        let result: ArtifactRevision = self
            .execute_path(
                "get_artifact_revision",
                Some(scope),
                &[],
                None,
                false,
                Some(artifact),
            )
            .await?;
        if result.scope_id != scope
            || result.family != ArtifactReadFamily::AtomicMemory
            || result.artifact_id != artifact.artifact_id
            || result.revision != artifact.revision
            || result.content.get("schema").and_then(|v| v.as_str())
                != Some("powercontext.atomic-memory.v1")
            || !result.artifacts.iter().all(valid_reference)
        {
            return Err(SafeError::InvalidResponse.into());
        }
        let kind = result
            .content
            .get("kind")
            .and_then(|v| v.as_str())
            .filter(|v| !v.trim().is_empty() && v.chars().count() <= 128)
            .ok_or(SafeError::InvalidResponse)?;
        let text = result
            .content
            .get("text")
            .and_then(|v| v.as_str())
            .filter(|v| !v.is_empty())
            .ok_or(SafeError::InvalidResponse)?;
        Ok(AtomicMemoryDetail {
            artifact: artifact.clone(),
            kind: kind.into(),
            text: text.into(),
            source_refs: result.sources,
        })
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
fn valid_reference(reference: &ArtifactReference) -> bool {
    (1..=9_007_199_254_740_991).contains(&reference.revision)
        && !reference.family.is_empty()
        && reference.family.len() <= 128
        && !reference.artifact_id.is_empty()
        && reference.artifact_id.len() <= 128
        && reference.artifact_id.bytes().all(|b| b.is_ascii_graphic())
}
fn valid_atomic_record(record: &AtomicMemoryRecord) -> bool {
    record.artifact.family == "atomic-memory"
        && valid_reference(&record.artifact)
        && (0..=9_007_199_254_740_991).contains(&record.state_version)
        && !record.kind.trim().is_empty()
        && record.kind.chars().count() <= 128
        && !record.text.is_empty()
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
