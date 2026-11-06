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

//! Read-only Runtime observations scoped to an already activated connection.
use super::{ConnectionManager, Fact, NativeIdentity};
use crate::{
    error::SafeError,
    transport::{ApiFailure, ServerApi, wire::*},
};
use serde::Serialize;
use std::{future::Future, sync::Arc};
use tokio::sync::watch;
use ts_rs::TS;

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeFact<T> {
    pub checked_at: u64,
    pub fact: Fact<T>,
}
impl<T> From<Result<T, ApiFailure>> for RuntimeFact<T> {
    fn from(result: Result<T, ApiFailure>) -> Self {
        Self {
            checked_at: std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs(),
            fact: result.into(),
        }
    }
}
#[derive(Serialize, TS)]
pub struct RuntimeCapabilities {
    pub memory_extraction: bool,
    pub handoff_generation: bool,
    pub search_modes: Vec<MemorySearchMode>,
    pub extraction: Option<ExtractionStatus>,
}

impl From<Capabilities> for RuntimeCapabilities {
    fn from(value: Capabilities) -> Self {
        let extraction = value.extraction.map(|mut extraction| {
            if let Some(failure) = extraction.observation.last_failure.as_mut()
                && !matches!(
                    failure.code.as_str(),
                    "model_configuration_error"
                        | "model_timeout"
                        | "model_unavailable"
                        | "invalid_model_output"
                        | "worker_timeout"
                        | "worker_crash"
                        | "invalid_worker_result"
                        | "missing_durable_acknowledgement"
                        | "supervisor_failed"
                        | "lease_renewal_failed"
                        | "scope_discovery_failed"
                        | "processing_failed"
                )
            {
                failure.code = "unknown".into();
            }
            extraction
        });
        Self {
            memory_extraction: value.memory_extraction,
            handoff_generation: value.handoff_generation,
            search_modes: value.search_modes,
            extraction,
        }
    }
}
struct RuntimeTarget {
    api: Arc<ServerApi>,
    server_id: Option<String>,
    identity: Option<NativeIdentity>,
    readiness_qualified: bool,
    changed: watch::Receiver<u32>,
    cancelled: watch::Receiver<u32>,
}
impl ConnectionManager {
    fn runtime_target(&self, generation: u32) -> Result<RuntimeTarget, SafeError> {
        let inner = self.lock()?;
        if generation != inner.generation {
            return Err(SafeError::StaleContext);
        }
        let active = inner.active.as_ref().ok_or(SafeError::NotConnected)?;
        let report = &active.view.report;
        Ok(RuntimeTarget {
            api: active.api.clone(),
            server_id: report.server_id().map(str::to_owned),
            identity: if let Some(fact) = &report.connection_identity {
                fact.value.clone().map(NativeIdentity::Connection)
            } else if report.compatibility_verified {
                report.identity.value.clone().map(NativeIdentity::Principal)
            } else {
                None
            },
            readiness_qualified: report
                .supported_operations
                .iter()
                .any(|op| op == "get_readiness"),
            changed: self.changed.subscribe(),
            cancelled: self.runtime_reads.subscribe(),
        })
    }

    pub fn cancel_runtime_reads(&self, generation: u32) -> Result<(), SafeError> {
        let inner = self.lock()?;
        if generation != inner.generation {
            return Err(SafeError::StaleContext);
        }
        self.runtime_reads
            .send_modify(|revision| *revision = revision.wrapping_add(1));
        Ok(())
    }

    // Observation verifies the selected identity contract without invalidating
    // independently authorized work when an observation is denied.
    async fn runtime_identity(target: &RuntimeTarget) -> Result<(), ApiFailure> {
        let identity = target
            .identity
            .as_ref()
            .ok_or(SafeError::CompatibilityUnverified)?;
        identity.verify(&target.api).await?;
        if matches!(identity, NativeIdentity::Principal(_)) {
            Self::deployment_unchanged(&target.api, target.server_id.as_deref()).await?;
        }
        Ok(())
    }

    async fn runtime_budget<T>(
        work: impl Future<Output = Result<T, ApiFailure>>,
    ) -> Result<T, ApiFailure> {
        tokio::time::timeout(std::time::Duration::from_secs(25), work)
            .await
            .map_err(|_| SafeError::Timeout)?
    }

    pub async fn observe_runtime_liveness(
        &self,
        generation: u32,
    ) -> Result<RuntimeFact<HealthResponse>, ApiFailure> {
        let target = self.runtime_target(generation)?;
        let (mut changed, mut cancelled) = (target.changed.clone(), target.cancelled.clone());
        let _permit = self
            .runtime_liveness_slot
            .try_acquire()
            .map_err(|_| SafeError::Busy)?;
        Self::cancellable(
            &mut changed,
            Self::cancellable(&mut cancelled, async { Ok(target.api.live().await.into()) }),
        )
        .await
    }

    pub async fn observe_runtime_capabilities(
        &self,
        generation: u32,
    ) -> Result<RuntimeFact<RuntimeCapabilities>, ApiFailure> {
        let target = self.runtime_target(generation)?;
        let (mut changed, mut cancelled) = (target.changed.clone(), target.cancelled.clone());
        let _permit = self
            .runtime_slot
            .try_acquire()
            .map_err(|_| SafeError::Busy)?;
        Self::cancellable(
            &mut changed,
            Self::cancellable(&mut cancelled, async {
                Ok(Self::runtime_budget(async {
                    Self::runtime_identity(&target).await?;
                    let result = target.api.capabilities().await?;
                    Self::runtime_identity(&target).await?;
                    Ok(result.into())
                })
                .await
                .into())
            }),
        )
        .await
    }

    pub async fn probe_runtime_readiness(
        &self,
        generation: u32,
    ) -> Result<RuntimeFact<ReadinessStatus>, ApiFailure> {
        let target = self.runtime_target(generation)?;
        let (mut changed, mut cancelled) = (target.changed.clone(), target.cancelled.clone());
        let _permit = self
            .runtime_slot
            .try_acquire()
            .map_err(|_| SafeError::Busy)?;
        Self::cancellable(
            &mut changed,
            Self::cancellable(&mut cancelled, async {
                let result = Self::runtime_budget(async {
                    Self::runtime_identity(&target).await?;
                    if !target.readiness_qualified {
                        return Err(SafeError::CompatibilityUnverified.into());
                    }
                    // The health URL is public. Desktop requires current observation access
                    // before sending a request that can exercise configured dependencies.
                    target.api.capabilities().await?;
                    Self::runtime_identity(&target).await?;
                    let result = RuntimeFact::from(
                        target.api.readiness().await.map(|response| response.status),
                    );
                    Self::runtime_identity(&target).await?;
                    Ok(result)
                })
                .await?;
                Ok(result)
            }),
        )
        .await
    }
}
