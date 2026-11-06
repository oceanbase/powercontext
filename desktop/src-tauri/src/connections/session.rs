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
use super::discovery::{DiscoveryResult, DiscoveryStatus};
use super::profiles::{ProfileInput, ProfileRepository, ProfileView};
use crate::{
    error::SafeError,
    session_observations::{
        ObservationOutcome, ObservationUnit, ObservedOperation, SessionObservations,
    },
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

mod runtime;
pub use runtime::{RuntimeCapabilities, RuntimeFact};

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
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub readiness_checked_at: Option<u64>,
    pub identity: Fact<AccessMeResponse>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub connection_identity: Option<Fact<ConnectionIdentity>>,
    pub capabilities: Fact<Capabilities>,
    pub compatibility_verified: bool,
    pub anonymous_access: bool,
    pub supported_operations: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub discovery: Option<DiscoveryResult>,
}
impl CheckReport {
    fn server_id(&self) -> Option<&str> {
        self.connection_identity
            .as_ref()
            .and_then(|fact| fact.value.as_ref())
            .map(|identity| identity.server_id.as_str())
            .or_else(|| {
                self.discovery
                    .as_ref()?
                    .server
                    .as_ref()
                    .map(|server| server.server_id.as_str())
            })
    }
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
    /// Separate current-client evidence; the original Server qualification remains unchanged.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    #[ts(optional)]
    pub authenticated_clients: Option<BTreeMap<String, String>>,
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
    pub server_id: Option<String>,
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
    pub references: Vec<ArtifactReference>,
    pub error: Option<ApiFailure>,
}
#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct WriteOutcome {
    pub record: WriteRecord,
    pub result: Option<crate::transport::MemorySaveResponse>,
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
enum NativeIdentity {
    Principal(AccessMeResponse),
    Connection(ConnectionIdentity),
}
impl NativeIdentity {
    async fn verify(&self, api: &ServerApi) -> Result<(), ApiFailure> {
        let unchanged = match self {
            Self::Principal(original) => api.principal().await.map(|current| current == *original),
            Self::Connection(original) => api
                .connection_identity()
                .await
                .map(|current| current == *original),
        }?;
        if unchanged {
            Ok(())
        } else {
            Err(SafeError::StaleContext.into())
        }
    }
}
struct ReadSnapshot {
    api: Arc<ServerApi>,
    server_id: Option<String>,
    identity: NativeIdentity,
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
    observations: SessionObservations,
    inner: Mutex<Inner>,
    changed: watch::Sender<u32>,
    scope_reads: watch::Sender<u32>,
    compatibility: Vec<CompatibilityProfile>,
    probes: tokio::sync::Semaphore,
    readiness_probes: tokio::sync::Semaphore,
    writes: tokio::sync::Semaphore,
    memory_reads: watch::Sender<u32>,
    stats_reads: watch::Sender<u32>,
    runtime_reads: watch::Sender<u32>,
    runtime_slot: tokio::sync::Semaphore,
    runtime_liveness_slot: tokio::sync::Semaphore,
}
impl ConnectionManager {
    pub fn new(profiles: ProfileRepository) -> Self {
        // No configuration becomes qualified merely because a health probe returned 200.
        let compatibility: Vec<CompatibilityProfile> =
            serde_json::from_str(include_str!("compatibility.json"))
                .expect("bundled compatibility manifest");
        #[cfg(feature = "ci-fixtures")]
        let compatibility = {
            let mut compatibility = compatibility;
            compatibility.push(
                serde_json::from_str(include_str!(concat!(
                    env!("OUT_DIR"),
                    "/ci-compatibility.json"
                )))
                .expect("explicit CI fixture qualification"),
            );
            compatibility
        };
        Self::with_compatibility(profiles, compatibility)
    }
    /// Supply explicit native qualification evidence, including synthetic test fixtures.
    /// This constructor is never exposed through IPC or renderer configuration.
    pub fn with_compatibility(
        profiles: ProfileRepository,
        compatibility: Vec<CompatibilityProfile>,
    ) -> Self {
        let (changed, _) = watch::channel(0);
        Self {
            observations: SessionObservations::default(),
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
            readiness_probes: tokio::sync::Semaphore::new(1),
            writes: tokio::sync::Semaphore::new(1),
            memory_reads: watch::channel(0).0,
            stats_reads: watch::channel(0).0,
            runtime_reads: watch::channel(0).0,
            runtime_slot: tokio::sync::Semaphore::new(1),
            runtime_liveness_slot: tokio::sync::Semaphore::new(1),
        }
    }
    fn lock(&self) -> Result<std::sync::MutexGuard<'_, Inner>, SafeError> {
        self.inner.lock().map_err(|_| SafeError::Storage)
    }
    pub fn observations(&self) -> &SessionObservations {
        &self.observations
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
            // A failed recheck must not expose a previously verified report.
            inner.reports.remove(id);
            if use_as_active {
                inner.active = None;
                self.invalidate(&mut inner)?;
            }
            (
                Arc::new(
                    inner
                        .profiles
                        .api(id)?
                        .with_observations(self.observations.clone()),
                ),
                profile,
                inner.generation,
                self.changed.subscribe(),
                use_as_active,
            )
        };
        let report = Self::cancellable(&mut cancelled, async {
            // Discovery is separate from the bounded observation batch (four transport slots).
            let discovery = api.server_info().await;
            let (live, identity, capabilities) =
                tokio::join!(api.live(), api.principal(), api.capabilities());
            let contract: serde_json::Value =
                serde_json::from_str(include_str!("../transport/operations.json"))
                    .map_err(|_| SafeError::InvalidResponse)?;
            let compatible = self.compatibility.iter().find(|c| {
                profile.compatibility.as_ref() == Some(&c.id)
                    && (contract["contractSha256"].as_str() == Some(&c.contract_sha256)
                        || (profile.authentication == super::profiles::Authentication::Bearer
                            && identity.is_ok()
                            && contract["contractSha256"].as_str().is_some_and(|digest| {
                                c.authenticated_clients
                                    .as_ref()
                                    .is_some_and(|clients| clients.contains_key(digest))
                            })))
                    && !identity
                        .as_ref()
                        .is_err_and(|e| e.code == SafeError::InvalidResponse)
                    && !capabilities
                        .as_ref()
                        .is_err_and(|e| e.code == SafeError::InvalidResponse)
            });
            let discovery = DiscoveryResult::assess(discovery, compatible);
            let mut supported_operations = discovery.supported_operations();
            let requires_identity = compatible.is_some_and(|profile| {
                profile
                    .operations
                    .iter()
                    .any(|op| op == "get_connection_identity")
            });
            let connection_identity = if requires_identity {
                let result = if supported_operations
                    .iter()
                    .any(|op| op == "get_connection_identity")
                {
                    api.connection_identity().await.and_then(|value| {
                        let deployment_matches = discovery
                            .server
                            .as_ref()
                            .map_or(discovery.status == DiscoveryStatus::Denied, |server| {
                                server.server_id == value.server_id
                            });
                        if deployment_matches
                            && (value.authentication != ConnectionAuthentication::Disabled
                                || profile.authentication
                                    == super::profiles::Authentication::UnauthenticatedLoopback)
                        {
                            Ok(value)
                        } else {
                            Err(SafeError::StaleContext.into())
                        }
                    })
                } else {
                    supported_operations.clear();
                    Err(SafeError::CompatibilityUnverified.into())
                };
                if result
                    .as_ref()
                    .is_err_and(|error| error.code == SafeError::StaleContext)
                {
                    return Err(SafeError::StaleContext.into());
                }
                Some(Fact::from(result))
            } else if identity
                .as_ref()
                .is_err_and(|error| error.code == SafeError::RuntimeNotReady)
            {
                // A generic 503 is not evidence that authentication or Access is disabled.
                Some(Fact {
                    value: None,
                    error: Some(SafeError::CompatibilityUnverified.into()),
                })
            } else {
                None
            };
            let anonymous_access = connection_identity
                .as_ref()
                .and_then(|fact| fact.value.as_ref())
                .is_some_and(|value| value.authentication == ConnectionAuthentication::Disabled);
            if requires_identity
                && connection_identity
                    .as_ref()
                    .and_then(|fact| fact.value.as_ref())
                    .is_none()
            {
                supported_operations.clear();
            }
            if !supported_operations.is_empty() {
                if let Some(value) = connection_identity
                    .as_ref()
                    .and_then(|fact| fact.value.as_ref())
                {
                    NativeIdentity::Connection(value.clone())
                        .verify(&api)
                        .await?;
                } else {
                    Self::deployment_unchanged(
                        &api,
                        discovery
                            .server
                            .as_ref()
                            .map(|server| server.server_id.as_str()),
                    )
                    .await?;
                }
            }
            Ok(CheckReport {
                anonymous_access,
                connection_id: id.into(),
                revision: profile.revision,
                checked_at: std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs(),
                liveness: live.into(),
                readiness: Fact {
                    value: None,
                    error: None,
                },
                readiness_checked_at: None,
                identity: identity.into(),
                connection_identity,
                capabilities: capabilities.into(),
                compatibility_verified: !supported_operations.is_empty(),
                supported_operations,
                discovery: Some(discovery),
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
    /// Explicit diagnostic action. Its result cannot activate or authorize a context.
    pub async fn probe_readiness(
        &self,
        id: &str,
        revision: u32,
    ) -> Result<DesktopState, ApiFailure> {
        let _permit = self
            .readiness_probes
            .try_acquire()
            .map_err(|_| SafeError::Busy)?;
        let (api, generation, server_id, identity, mut cancelled) = {
            let inner = self.lock()?;
            let report = inner.reports.get(id).ok_or(SafeError::NotConnected)?;
            if report.revision != revision {
                return Err(SafeError::StaleContext.into());
            }
            (
                inner
                    .profiles
                    .api(id)?
                    .with_observations(self.observations.clone()),
                inner.generation,
                report.server_id().map(str::to_owned),
                report
                    .connection_identity
                    .as_ref()
                    .and_then(|fact| fact.value.clone())
                    .map(NativeIdentity::Connection),
                self.changed.subscribe(),
            )
        };
        let result = Self::cancellable(&mut cancelled, async {
            if let Some(identity) = &identity {
                identity.verify(&api).await?;
            } else {
                Self::deployment_unchanged(&api, server_id.as_deref()).await?;
            }
            let result = api.readiness().await;
            if let Some(identity) = &identity {
                identity.verify(&api).await?;
            } else {
                Self::deployment_unchanged(&api, server_id.as_deref()).await?;
            }
            Ok(result)
        })
        .await?;
        let mut inner = self.lock()?;
        if inner.generation != generation {
            return Err(SafeError::StaleContext.into());
        }
        let report = inner.reports.get_mut(id).ok_or(SafeError::StaleContext)?;
        if report.revision != revision {
            return Err(SafeError::StaleContext.into());
        }
        report.readiness = result.into();
        report.readiness_checked_at = Some(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
        );
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
        let identity = match &active.view.report.connection_identity {
            Some(fact) => NativeIdentity::Connection(fact.value.clone().ok_or_else(|| {
                fact.error
                    .as_ref()
                    .map_or(SafeError::Unauthorized, |error| error.code)
            })?),
            None => NativeIdentity::Principal(
                active
                    .view
                    .report
                    .identity
                    .value
                    .clone()
                    .ok_or(SafeError::Unauthorized)?,
            ),
        };
        Ok(ReadSnapshot {
            api: active.api.clone(),
            server_id: active.view.report.server_id().map(str::to_owned),
            identity,
            cancelled: self.changed.subscribe(),
        })
    }
    async fn deployment_unchanged(
        api: &ServerApi,
        original: Option<&str>,
    ) -> Result<(), ApiFailure> {
        // Re-observation never creates qualification or silently upgrades a legacy session.
        let current = DiscoveryResult::assess(api.server_info().await, None);
        let unchanged = match current.status {
            DiscoveryStatus::Supported => original.is_some_and(|id| {
                current
                    .server
                    .as_ref()
                    .is_some_and(|server| server.server_id == id)
            }),
            DiscoveryStatus::Missing => original.is_none(),
            _ => false,
        };
        if unchanged {
            Ok(())
        } else {
            Err(current
                .error
                .unwrap_or_else(|| SafeError::StaleContext.into()))
        }
    }
    async fn identity_unchanged(
        &self,
        api: &ServerApi,
        original: &NativeIdentity,
        server_id: Option<&str>,
        generation: u32,
    ) -> Result<(), ApiFailure> {
        let result = match original.verify(api).await {
            Ok(()) if matches!(original, NativeIdentity::Principal(_)) => {
                Self::deployment_unchanged(api, server_id).await
            }
            result => result,
        };
        if result.is_ok() {
            return result;
        }
        let mut inner = self.lock()?;
        if inner.generation == generation {
            if let Some(active) = inner.active.take() {
                inner.reports.remove(&active.view.connection_id);
            }
            self.invalidate(&mut inner)?;
        }
        result
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
        self.observations
            .user_operation(ObservedOperation::ListScopes, async {
                let ReadSnapshot {
                    api,
                    server_id,
                    identity,
                    mut cancelled,
                } = self.snapshot(generation, "list_scopes")?;
                let mut query_cancelled = self.begin_scope_query(generation)?;
                Self::cancellable(
                    &mut cancelled,
                    Self::cancellable(&mut query_cancelled, async {
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        let result = api.scopes(query, cursor).await;
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        result
                    }),
                )
                .await
            })
            .await
    }
    pub async fn default_scope(&self, generation: u32) -> Result<ScopeDescriptor, ApiFailure> {
        self.observations
            .user_operation(ObservedOperation::GetDefaultScope, async {
                let ReadSnapshot {
                    api,
                    server_id,
                    identity,
                    mut cancelled,
                } = self.snapshot(generation, "get_default_scope")?;
                let mut query_cancelled = self.begin_scope_query(generation)?;
                Self::cancellable(
                    &mut cancelled,
                    Self::cancellable(&mut query_cancelled, async {
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        let result = api.default_scope().await;
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        result
                    }),
                )
                .await
            })
            .await
    }
    pub async fn select_scope(
        &self,
        generation: u32,
        id: &str,
    ) -> Result<DesktopState, ApiFailure> {
        self.observations
            .user_operation(ObservedOperation::GetScope, async {
                let ReadSnapshot {
                    api,
                    server_id,
                    identity,
                    mut cancelled,
                } = self.snapshot(generation, "get_scope")?;
                let scope = Self::cancellable(&mut cancelled, async {
                    self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                        .await?;
                    let result = api.scope(id).await;
                    self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                        .await?;
                    result
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
            })
            .await
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
        let principal = match &snapshot.identity {
            NativeIdentity::Principal(value) => Some(value.principal.clone()),
            NativeIdentity::Connection(value) => value.principal.clone(),
        };
        let server_id = snapshot.server_id.clone();
        Ok((
            snapshot,
            MemoryContext {
                endpoint,
                server_id,
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
    fn begin_stats_read(&self, generation: u32) -> Result<watch::Receiver<u32>, SafeError> {
        let inner = self.lock()?;
        if inner.generation != generation {
            return Err(SafeError::StaleContext);
        }
        let next = self
            .stats_reads
            .borrow()
            .checked_add(1)
            .ok_or(SafeError::Storage)?;
        self.stats_reads.send_replace(next);
        Ok(self.stats_reads.subscribe())
    }
    pub fn cancel_stats_reads(&self, generation: u32) -> Result<(), SafeError> {
        self.begin_stats_read(generation).map(|_| ())
    }
    pub async fn stats(
        &self,
        generation: u32,
        period: crate::transport::wire::StatsPeriod,
    ) -> Result<crate::transport::ExactScopeStatistics, ApiFailure> {
        self.observations
            .user_operation(ObservedOperation::GetStats, async {
                let (
                    ReadSnapshot {
                        api,
                        server_id,
                        identity,
                        mut cancelled,
                    },
                    context,
                ) = self.memory_snapshot(generation, "get_stats")?;
                let mut query_cancelled = self.begin_stats_read(generation)?;
                Self::cancellable(
                    &mut cancelled,
                    Self::cancellable(&mut query_cancelled, async {
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        let result = api.stats(&context.scope_id, period).await;
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        result
                    }),
                )
                .await
            })
            .await
    }
    pub async fn search_memory(
        &self,
        generation: u32,
        query: &str,
    ) -> Result<crate::transport::MemorySearchResponse, ApiFailure> {
        self.observations
            .user_operation(ObservedOperation::SearchMemory, async {
                let (
                    ReadSnapshot {
                        api,
                        server_id,
                        identity,
                        mut cancelled,
                    },
                    context,
                ) = self.memory_snapshot(generation, "search_memory")?;
                let mut query_cancelled = self.begin_memory_read(generation)?;
                Self::cancellable(
                    &mut cancelled,
                    Self::cancellable(&mut query_cancelled, async {
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        let result = api.search(&context.scope_id, query).await;
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        result
                    }),
                )
                .await
            })
            .await
    }
    pub async fn atomic_memory_entry(
        &self,
        generation: u32,
        artifact: &ArtifactReference,
    ) -> Result<crate::transport::AtomicMemoryDetail, ApiFailure> {
        self.observations
            .user_operation(ObservedOperation::GetArtifactRevision, async {
                let (
                    ReadSnapshot {
                        api,
                        server_id,
                        identity,
                        mut cancelled,
                    },
                    context,
                ) = self.memory_snapshot(generation, "get_artifact_revision")?;
                let mut query_cancelled = self.begin_memory_read(generation)?;
                Self::cancellable(
                    &mut cancelled,
                    Self::cancellable(&mut query_cancelled, async {
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        let result = api.atomic_entry(&context.scope_id, artifact).await;
                        self.identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                            .await?;
                        result
                    }),
                )
                .await
            })
            .await
    }
    pub async fn remember(&self, generation: u32, text: &str) -> Result<WriteOutcome, ApiFailure> {
        let mut observation = self.observations.begin(
            ObservationUnit::UserOperation,
            ObservedOperation::RememberMemory,
        );
        let result = async {
            let _permit = self.writes.try_acquire().map_err(|_| SafeError::Busy)?;
            crate::transport::validate_text(text)?;
            let (
                ReadSnapshot {
                    api,
                    server_id,
                    identity,
                    mut cancelled,
                },
                context,
            ) = self.memory_snapshot(generation, "remember_memory")?;
            Self::cancellable(
                &mut cancelled,
                self.identity_unchanged(&api, &identity, server_id.as_deref(), generation),
            )
            .await?;
            let mut record = WriteRecord {
                operation_id: uuid::Uuid::new_v4().to_string(),
                context,
                status: WriteStatus::Pending,
                references: vec![],
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
            observation.write_dispatched();
            let response = api.remember(&record.context.scope_id, text).await;
            let identity_result = self
                .identity_unchanged(&api, &identity, server_id.as_deref(), generation)
                .await;
            let result = match response {
                Ok(value) => {
                    if let Err(error) = identity_result {
                        // A successful HTTP reply cannot establish which deployment accepted the write.
                        record.status = WriteStatus::Unknown;
                        record.error = Some(error);
                        None
                    } else {
                        record.status = WriteStatus::Succeeded;
                        record.references = value
                            .records
                            .iter()
                            .map(|record| record.artifact.clone())
                            .collect();
                        Some(value)
                    }
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
        .await;
        match &result {
            Ok(outcome) => observation.write_finished(
                match outcome.record.status {
                    WriteStatus::Succeeded => ObservationOutcome::Success,
                    WriteStatus::Failed => ObservationOutcome::Failure,
                    WriteStatus::Unknown | WriteStatus::Pending => ObservationOutcome::Unknown,
                },
                outcome.record.error.as_ref().map(|error| error.code),
            ),
            Err(_) => observation.finish(&result),
        }
        result
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
