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

//! Native-owned connection and Scope context. Viewing a profile never activates it.
use super::profiles::{ProfileInput, ProfileRepository, ProfileView};
use crate::{
    error::SafeError,
    transport::{ApiFailure, ServerApi, wire::*},
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    future::Future,
    sync::{Arc, Mutex},
};
use tokio::sync::watch;
use ts_rs::TS;

#[derive(Clone, Serialize, TS)]
pub struct Fact<T> {
    pub value: Option<T>,
    pub error: Option<ApiFailure>,
}
impl<T> From<Result<T, ApiFailure>> for Fact<T> {
    fn from(result: Result<T, ApiFailure>) -> Self {
        match result {
            Ok(value) => Self {
                value: Some(value),
                error: None,
            },
            Err(error) => Self {
                value: None,
                error: Some(error),
            },
        }
    }
}
#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct CheckReport {
    pub connection_id: String,
    pub revision: u32,
    pub checked_at: u64,
    pub liveness: Fact<HealthResponse>,
    pub readiness: Fact<ReadinessResponse>,
    pub identity: Fact<AccessMeResponse>,
    pub capabilities: Fact<Capabilities>,
    pub compatibility_verified: bool,
    pub anonymous_access: bool,
    pub supported_operations: Vec<String>,
}
#[derive(Clone, Deserialize, Serialize, TS)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CompatibilityProfile {
    pub id: String,
    pub server_commit: String,
    pub contract_sha256: String,
    pub artifact_sha256: String,
    pub operations: Vec<String>,
    pub evidence: String,
}
#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ActiveView {
    pub connection_id: String,
    pub generation: u32,
    pub report: CheckReport,
    pub scope: Option<ScopeDescriptor>,
}
#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DesktopState {
    pub generation: u32,
    pub profiles: Vec<ProfileView>,
    pub reports: Vec<CheckReport>,
    pub active: Option<ActiveView>,
    pub compatibility_profiles: Vec<CompatibilityProfile>,
    pub pending_credential_cleanup: usize,
    pub last_write: Option<WriteRecord>,
}
#[derive(Clone, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct MemoryContext {
    pub connection_id: String,
    pub endpoint: String,
    pub principal: Option<AccessPrincipal>,
    pub generation: u32,
    pub scope_id: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum WriteStatus {
    Pending,
    Succeeded,
    Failed,
    Unknown,
}
#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct WriteRecord {
    pub operation_id: String,
    pub context: MemoryContext,
    pub status: WriteStatus,
    pub citation: Option<MemoryCitation>,
    pub error: Option<ApiFailure>,
}
#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct WriteOutcome {
    pub record: WriteRecord,
    pub result: Option<MemoryMutationResponse>,
}
struct WriteGuard<'a> {
    manager: &'a ConnectionManager,
    armed: bool,
}
impl Drop for WriteGuard<'_> {
    fn drop(&mut self) {
        if self.armed
            && let Ok(mut inner) = self.manager.lock()
            && let Some(record) = &mut inner.last_write
        {
            record.status = WriteStatus::Unknown;
        }
    }
}
struct ReadSnapshot {
    api: Arc<ServerApi>,
    identity: Option<AccessMeResponse>,
    cancelled: watch::Receiver<u32>,
}
struct Active {
    view: ActiveView,
    api: Arc<ServerApi>,
}
struct Inner {
    profiles: ProfileRepository,
    reports: BTreeMap<String, CheckReport>,
    active: Option<Active>,
    generation: u32,
    last_write: Option<WriteRecord>,
}
pub struct ConnectionManager {
    inner: Mutex<Inner>,
    changed: watch::Sender<u32>,
    scope_reads: watch::Sender<u32>,
    compatibility: Vec<CompatibilityProfile>,
    probes: tokio::sync::Semaphore,
    writes: tokio::sync::Semaphore,
    memory_reads: watch::Sender<u32>,
}
impl ConnectionManager {
    pub fn new(profiles: ProfileRepository) -> Self {
        let (changed, _) = watch::channel(0);
        // No configuration becomes qualified merely because a health probe returned 200.
        let compatibility = serde_json::from_str(include_str!("compatibility.json"))
            .expect("bundled compatibility manifest");
        Self {
            inner: Mutex::new(Inner {
                profiles,
                reports: BTreeMap::new(),
                active: None,
                generation: 0,
                last_write: None,
            }),
            changed,
            scope_reads: watch::channel(0).0,
            compatibility,
            probes: tokio::sync::Semaphore::new(1),
            writes: tokio::sync::Semaphore::new(1),
            memory_reads: watch::channel(0).0,
        }
    }
    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Inner>, SafeError> {
        self.inner.lock().map_err(|_| SafeError::Storage)
    }
    fn invalidate(&self, inner: &mut Inner) -> Result<(), SafeError> {
        inner.generation = inner.generation.checked_add(1).ok_or(SafeError::Storage)?;
        self.changed.send_replace(inner.generation);
        if let Some(active) = &mut inner.active {
            active.view.generation = inner.generation;
        }
        Ok(())
    }
    pub fn state(&self) -> Result<DesktopState, SafeError> {
        let inner = self.lock()?;
        Ok(DesktopState {
            generation: inner.generation,
            profiles: inner.profiles.views(),
            reports: inner.reports.values().cloned().collect(),
            active: inner.active.as_ref().map(|a| a.view.clone()),
            compatibility_profiles: self.compatibility.clone(),
            pending_credential_cleanup: inner.profiles.pending_cleanup(),
            last_write: inner.last_write.clone(),
        })
    }
    pub fn save_profile(&self, input: ProfileInput) -> Result<DesktopState, SafeError> {
        let mut inner = self.lock()?;
        // Editing an active connection immediately drops its authorized context, including failed edits.
        if input.id.as_ref().is_some_and(|id| {
            inner
                .active
                .as_ref()
                .is_some_and(|a| &a.view.connection_id == id)
        }) {
            inner.active = None;
        }
        self.invalidate(&mut inner)?;
        if let Some(id) = &input.id {
            inner.reports.remove(id);
        }
        inner.profiles.save(input)?;
        drop(inner);
        self.state()
    }
    pub fn remove_profile(&self, id: &str, revision: u32) -> Result<DesktopState, SafeError> {
        let mut inner = self.lock()?;
        inner.profiles.remove(id, revision)?;
        if inner
            .active
            .as_ref()
            .is_some_and(|a| a.view.connection_id == id)
        {
            inner.active = None;
        }
        inner.reports.remove(id);
        self.invalidate(&mut inner)?;
        drop(inner);
        self.state()
    }
    pub fn disconnect(&self) -> Result<DesktopState, SafeError> {
        let mut inner = self.lock()?;
        inner.active = None;
        self.invalidate(&mut inner)?;
        drop(inner);
        self.state()
    }
    pub fn invalidate_profile(&self, id: &str) -> Result<DesktopState, SafeError> {
        let mut inner = self.lock()?;
        inner.reports.remove(id);
        if inner
            .active
            .as_ref()
            .is_some_and(|a| a.view.connection_id == id)
        {
            inner.active = None;
        }
        self.invalidate(&mut inner)?;
        drop(inner);
        self.state()
    }
    pub async fn check(&self, id: &str, activate: bool) -> Result<DesktopState, ApiFailure> {
        let _permit = self.probes.try_acquire().map_err(|_| SafeError::Busy)?;
        let (api, profile, generation, mut cancelled, use_as_active) = {
            let mut inner = self.lock()?;
            let profile = inner
                .profiles
                .views()
                .into_iter()
                .find(|p| p.id == id)
                .ok_or(SafeError::NotFound)?;
            let use_as_active = activate
                || inner
                    .active
                    .as_ref()
                    .is_some_and(|a| a.view.connection_id == id);
            if use_as_active {
                inner.active = None;
                self.invalidate(&mut inner)?;
            }
            (
                Arc::new(inner.profiles.api(id)?),
                profile,
                inner.generation,
                self.changed.subscribe(),
                use_as_active,
            )
        };
        let report = Self::cancellable(&mut cancelled, async {
            let (live, ready, identity, capabilities) = tokio::join!(
                api.live(),
                api.readiness(),
                api.principal(),
                api.capabilities()
            );
            let anonymous_access = profile.authentication
                == super::profiles::Authentication::UnauthenticatedLoopback
                && ready
                    .as_ref()
                    .is_ok_and(|r| r.checks.get("access_mode").is_some_and(|v| v == "disabled"))
                && identity
                    .as_ref()
                    .is_err_and(|e| e.code == SafeError::RuntimeNotReady);
            let contract: serde_json::Value =
                serde_json::from_str(include_str!("../transport/operations.json"))
                    .map_err(|_| SafeError::InvalidResponse)?;
            let compatible = self.compatibility.iter().find(|c| {
                profile.compatibility.as_ref() == Some(&c.id)
                    && contract["contractSha256"].as_str() == Some(&c.contract_sha256)
                    && !identity
                        .as_ref()
                        .is_err_and(|e| e.code == SafeError::InvalidResponse)
                    && !capabilities
                        .as_ref()
                        .is_err_and(|e| e.code == SafeError::InvalidResponse)
            });
            Ok(CheckReport {
                anonymous_access,
                connection_id: id.into(),
                revision: profile.revision,
                checked_at: std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs(),
                liveness: live.into(),
                readiness: ready.into(),
                identity: identity.into(),
                capabilities: capabilities.into(),
                compatibility_verified: compatible.is_some(),
                supported_operations: compatible.map(|c| c.operations.clone()).unwrap_or_default(),
            })
        })
        .await?;
        let mut inner = self.lock()?;
        if inner.generation != generation {
            return Err(SafeError::StaleContext.into());
        }
        inner.reports.insert(id.into(), report.clone());
        // Activation can expose diagnostic facts while business operations remain independently gated.
        if use_as_active {
            inner.active = Some(Active {
                view: ActiveView {
                    connection_id: id.into(),
                    generation,
                    report,
                    scope: None,
                },
                api,
            });
        }
        drop(inner);
        self.state().map_err(Into::into)
    }
    fn snapshot(&self, generation: u32, operation: &str) -> Result<ReadSnapshot, SafeError> {
        let inner = self.lock()?;
        let active = inner.active.as_ref().ok_or(SafeError::NotConnected)?;
        if generation != inner.generation {
            return Err(SafeError::StaleContext);
        }
        if !active.view.report.compatibility_verified
            || !active
                .view
                .report
                .supported_operations
                .iter()
                .any(|op| op == operation)
        {
            return Err(SafeError::CompatibilityUnverified);
        }
        let principal = active.view.report.identity.value.clone();
        if principal.is_none() && !active.view.report.anonymous_access {
            return Err(SafeError::Unauthorized);
        }
        Ok(ReadSnapshot {
            api: active.api.clone(),
            identity: principal,
            cancelled: self.changed.subscribe(),
        })
    }
    async fn identity_unchanged(
        &self,
        api: &ServerApi,
        original: &Option<AccessMeResponse>,
        generation: u32,
    ) -> Result<(), ApiFailure> {
        let current = api.principal().await;
        let unchanged = if let Some(original) = original {
            current.as_ref().is_ok_and(|value| value == original)
        } else {
            current
                .as_ref()
                .is_err_and(|e| e.code == SafeError::RuntimeNotReady)
                && api
                    .readiness()
                    .await
                    .is_ok_and(|r| r.checks.get("access_mode").is_some_and(|v| v == "disabled"))
        };
        if unchanged {
            return Ok(());
        }
        let mut inner = self.lock()?;
        if inner.generation == generation {
            if let Some(active) = inner.active.take() {
                inner.reports.remove(&active.view.connection_id);
            }
            self.invalidate(&mut inner)?;
        }
        match current {
            Err(error) => Err(error),
            Ok(_) => Err(SafeError::StaleContext.into()),
        }
    }
    fn begin_scope_query(&self, generation: u32) -> Result<watch::Receiver<u32>, SafeError> {
        let inner = self.lock()?;
        if generation != inner.generation {
            return Err(SafeError::StaleContext);
        }
        if inner.active.is_none() {
            return Err(SafeError::NotConnected);
        }
        let next = self
            .scope_reads
            .borrow()
            .checked_add(1)
            .ok_or(SafeError::Storage)?;
        self.scope_reads.send_replace(next);
        Ok(self.scope_reads.subscribe())
    }
    pub fn cancel_scope_reads(&self, generation: u32) -> Result<(), SafeError> {
        self.begin_scope_query(generation).map(|_| ())
    }
    pub async fn scopes(
        &self,
        generation: u32,
        query: &str,
        cursor: Option<&str>,
    ) -> Result<ScopePage, ApiFailure> {
        let ReadSnapshot {
            api,
            identity,
            mut cancelled,
        } = self.snapshot(generation, "list_scopes")?;
        let mut query_cancelled = self.begin_scope_query(generation)?;
        Self::cancellable(
            &mut cancelled,
            Self::cancellable(&mut query_cancelled, async {
                self.identity_unchanged(&api, &identity, generation).await?;
                api.scopes(query, cursor).await
            }),
        )
        .await
    }
    pub async fn default_scope(&self, generation: u32) -> Result<ScopeDescriptor, ApiFailure> {
        let ReadSnapshot {
            api,
            identity,
            mut cancelled,
        } = self.snapshot(generation, "get_default_scope")?;
        let mut query_cancelled = self.begin_scope_query(generation)?;
        Self::cancellable(
            &mut cancelled,
            Self::cancellable(&mut query_cancelled, async {
                self.identity_unchanged(&api, &identity, generation).await?;
                api.default_scope().await
            }),
        )
        .await
    }
    pub async fn select_scope(
        &self,
        generation: u32,
        id: &str,
    ) -> Result<DesktopState, ApiFailure> {
        let ReadSnapshot {
            api,
            identity,
            mut cancelled,
        } = self.snapshot(generation, "get_scope")?;
        let scope = Self::cancellable(&mut cancelled, async {
            self.identity_unchanged(&api, &identity, generation).await?;
            api.scope(id).await
        })
        .await?;
        let mut inner = self.lock()?;
        if generation != inner.generation {
            return Err(SafeError::StaleContext.into());
        }
        self.invalidate(&mut inner)?;
        inner
            .active
            .as_mut()
            .ok_or(SafeError::NotConnected)?
            .view
            .scope = Some(scope);
        drop(inner);
        self.state().map_err(Into::into)
    }
    fn memory_snapshot(
        &self,
        generation: u32,
        operation: &str,
    ) -> Result<(ReadSnapshot, MemoryContext), SafeError> {
        let snapshot = self.snapshot(generation, operation)?;
        let inner = self.lock()?;
        if inner.generation != generation {
            return Err(SafeError::StaleContext);
        }
        let active = inner.active.as_ref().ok_or(SafeError::NotConnected)?;
        let scope = active.view.scope.as_ref().ok_or(SafeError::ScopeRequired)?;
        let endpoint = inner
            .profiles
            .views()
            .into_iter()
            .find(|profile| profile.id == active.view.connection_id)
            .ok_or(SafeError::NotConnected)?
            .endpoint;
        let principal = snapshot
            .identity
            .as_ref()
            .map(|value| value.principal.clone());
        Ok((
            snapshot,
            MemoryContext {
                endpoint,
                principal,
                connection_id: active.view.connection_id.clone(),
                generation,
                scope_id: scope.scope_id.clone(),
            },
        ))
    }
    fn begin_memory_read(&self, generation: u32) -> Result<watch::Receiver<u32>, SafeError> {
        let inner = self.lock()?;
        if inner.generation != generation {
            return Err(SafeError::StaleContext);
        }
        let next = self
            .memory_reads
            .borrow()
            .checked_add(1)
            .ok_or(SafeError::Storage)?;
        self.memory_reads.send_replace(next);
        Ok(self.memory_reads.subscribe())
    }
    pub fn cancel_memory_reads(&self, generation: u32) -> Result<(), SafeError> {
        self.begin_memory_read(generation).map(|_| ())
    }
    pub async fn search_memory(
        &self,
        generation: u32,
        query: &str,
    ) -> Result<SearchMemoryResponse, ApiFailure> {
        let (
            ReadSnapshot {
                api,
                identity,
                mut cancelled,
            },
            context,
        ) = self.memory_snapshot(generation, "search_memory")?;
        let mut query_cancelled = self.begin_memory_read(generation)?;
        Self::cancellable(
            &mut cancelled,
            Self::cancellable(&mut query_cancelled, async {
                self.identity_unchanged(&api, &identity, generation).await?;
                api.search(&context.scope_id, query).await
            }),
        )
        .await
    }
    pub async fn memory_entry(
        &self,
        generation: u32,
        citation: &MemoryCitation,
    ) -> Result<MemoryEntry, ApiFailure> {
        let (
            ReadSnapshot {
                api,
                identity,
                mut cancelled,
            },
            context,
        ) = self.memory_snapshot(generation, "get_memory_entry")?;
        let mut query_cancelled = self.begin_memory_read(generation)?;
        Self::cancellable(
            &mut cancelled,
            Self::cancellable(&mut query_cancelled, async {
                self.identity_unchanged(&api, &identity, generation).await?;
                api.entry(&context.scope_id, citation).await
            }),
        )
        .await
    }
    pub async fn remember(&self, generation: u32, text: &str) -> Result<WriteOutcome, ApiFailure> {
        let _permit = self.writes.try_acquire().map_err(|_| SafeError::Busy)?;
        crate::transport::validate_text(text)?;
        let (
            ReadSnapshot {
                api,
                identity,
                mut cancelled,
            },
            context,
        ) = self.memory_snapshot(generation, "remember_memory")?;
        Self::cancellable(
            &mut cancelled,
            self.identity_unchanged(&api, &identity, generation),
        )
        .await?;
        let mut record = WriteRecord {
            operation_id: uuid::Uuid::new_v4().to_string(),
            context,
            status: WriteStatus::Pending,
            citation: None,
            error: None,
        };
        {
            let mut inner = self.lock()?;
            if inner.generation != generation {
                return Err(SafeError::StaleContext.into());
            }
            inner.last_write = Some(record.clone());
        }
        // No context cancellation after this point: the immutable API and Scope own the dispatched write.
        let mut guard = WriteGuard {
            manager: self,
            armed: true,
        };
        let response = api.remember(&record.context.scope_id, text).await;
        let result = match response {
            Ok(value) => {
                record.status = WriteStatus::Succeeded;
                record.citation = value.entry.as_ref().map(|e| e.citation.clone());
                Some(value)
            }
            Err(error) => {
                record.status = if error.dispatched
                    && !matches!(
                        error.code,
                        SafeError::Unauthorized
                            | SafeError::Forbidden
                            | SafeError::InvalidInput
                            | SafeError::NotFound
                            | SafeError::Conflict
                            | SafeError::AuthenticationUnavailable
                            | SafeError::RuntimeNotReady
                            | SafeError::Redirect
                    ) {
                    WriteStatus::Unknown
                } else {
                    WriteStatus::Failed
                };
                record.error = Some(error);
                None
            }
        };
        let mut inner = self.lock()?;
        inner.last_write = Some(record.clone());
        guard.armed = false;
        // A late response may report its original target, but may not disclose old body text in a new context.
        let result = if inner.generation == generation {
            result
        } else {
            None
        };
        Ok(WriteOutcome { record, result })
    }
    async fn cancellable<T>(
        cancelled: &mut watch::Receiver<u32>,
        work: impl Future<Output = Result<T, ApiFailure>>,
    ) -> Result<T, ApiFailure> {
        tokio::select! { biased;
            _ = cancelled.changed() => Err(SafeError::StaleContext.into()),
            value = work => value,
        }
    }
}
