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

//! Content-free, process-local observations. These are not a persistence or audit ledger.
use crate::{error::SafeError, transport::ApiFailure};
use serde::Serialize;
use std::{
    collections::{BTreeMap, VecDeque},
    future::Future,
    sync::{
        Arc, Mutex, TryLockError,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Instant, SystemTime, UNIX_EPOCH},
};
use ts_rs::TS;

mod ops;
pub(crate) use ops::OpsCallObservation;
pub use ops::{OpsSessionObservation, OpsSessionSummary};

pub const SESSION_OBSERVATION_LIMIT: usize = 512;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum ObservationUnit {
    HttpRequest,
    UserOperation,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum ObservedOperation {
    GetLiveness,
    GetServerInfo,
    GetReadiness,
    GetCapabilities,
    GetAccessPrincipal,
    ListScopes,
    GetScope,
    GetDefaultScope,
    RememberMemory,
    SearchMemory,
    GetArtifactRevision,
}
impl ObservedOperation {
    pub(crate) fn id(self) -> &'static str {
        match self {
            Self::GetLiveness => "get_liveness",
            Self::GetServerInfo => "get_server_info",
            Self::GetReadiness => "get_readiness",
            Self::GetCapabilities => "get_capabilities",
            Self::GetAccessPrincipal => "get_access_principal",
            Self::ListScopes => "list_scopes",
            Self::GetScope => "get_scope",
            Self::GetDefaultScope => "get_default_scope",
            Self::RememberMemory => "remember_memory",
            Self::SearchMemory => "search_memory",
            Self::GetArtifactRevision => "get_artifact_revision",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum ObservationOutcome {
    Success,
    Failure,
    Cancelled,
    Unknown,
}

#[derive(Clone, Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SessionObservation {
    pub unit: ObservationUnit,
    pub operation: ObservedOperation,
    pub outcome: ObservationOutcome,
    pub completed_at_ms: u64,
    pub duration_ms: f64,
    pub http_status: Option<u16>,
    pub error_code: Option<SafeError>,
}

#[derive(Clone, Debug, Default, Serialize, TS)]
pub struct OutcomeCounts {
    pub success: u32,
    pub failure: u32,
    pub cancelled: u32,
    pub unknown: u32,
}

#[derive(Clone, Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OperationSummary {
    pub unit: ObservationUnit,
    pub operation: ObservedOperation,
    pub outcomes: OutcomeCounts,
    pub sample_count: u32,
    pub min_duration_ms: f64,
    pub mean_duration_ms: f64,
    pub max_duration_ms: f64,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SessionCoverage {
    pub desktop_session_only: bool,
    pub persistence_evidence: bool,
    pub http_operations: Vec<ObservedOperation>,
    pub user_operations: Vec<ObservedOperation>,
    pub identity_checks_included: bool,
    pub installer_ops: bool,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct SessionSummary {
    pub schema_version: u8,
    pub session_started_at_ms: u64,
    pub window_started_at_ms: u64,
    pub generated_at_ms: u64,
    pub capacity: usize,
    pub evicted: u64,
    /// Process-lifetime loss count, deliberately not cleared by a window reset.
    pub dropped_since_start: u64,
    pub coverage: SessionCoverage,
    pub operations: Vec<OperationSummary>,
    pub recent: Vec<SessionObservation>,
    pub installer_ops: OpsSessionSummary,
}

struct Window {
    generation: u64,
    started_at_ms: u64,
    evicted: u64,
    records: VecDeque<SessionObservation>,
    ops_records: VecDeque<OpsSessionObservation>,
    ops_evicted: u64,
}
struct Shared {
    session_started_at_ms: u64,
    generation: AtomicU64,
    dropped: AtomicU64,
    ops_enabled: AtomicBool,
    ops_dropped: AtomicU64,
    window: Mutex<Window>,
}

#[derive(Clone)]
pub struct SessionObservations(Arc<Shared>);
impl Default for SessionObservations {
    fn default() -> Self {
        let started_at_ms = now_ms();
        Self(Arc::new(Shared {
            session_started_at_ms: started_at_ms,
            generation: AtomicU64::new(0),
            dropped: AtomicU64::new(0),
            ops_enabled: AtomicBool::new(false),
            ops_dropped: AtomicU64::new(0),
            window: Mutex::new(Window {
                generation: 0,
                started_at_ms,
                evicted: 0,
                records: VecDeque::with_capacity(SESSION_OBSERVATION_LIMIT),
                ops_records: VecDeque::with_capacity(ops::OPS_OBSERVATION_LIMIT),
                ops_evicted: 0,
            }),
        }))
    }
}
impl SessionObservations {
    pub(crate) fn enable_ops(&self) {
        self.0.ops_enabled.store(true, Ordering::Release);
    }

    pub(crate) async fn user_operation<T>(
        &self,
        operation: ObservedOperation,
        work: impl Future<Output = Result<T, ApiFailure>>,
    ) -> Result<T, ApiFailure> {
        let mut observation = self.begin(ObservationUnit::UserOperation, operation);
        let result = work.await;
        observation.finish(&result);
        result
    }

    pub(crate) fn begin(
        &self,
        unit: ObservationUnit,
        operation: ObservedOperation,
    ) -> ObservationGuard {
        ObservationGuard {
            observations: self.clone(),
            generation: self.0.generation.load(Ordering::Acquire),
            started: Instant::now(),
            unit,
            operation,
            outcome: ObservationOutcome::Cancelled,
            error_code: None,
            http_status: None,
        }
    }

    fn record(&self, generation: u64, observation: SessionObservation) {
        // Observation contention/poisoning must never delay or fail the owning operation, including Drop.
        match self.0.window.try_lock() {
            Ok(mut window) if window.generation == generation => {
                if window.records.len() == SESSION_OBSERVATION_LIMIT {
                    window.records.pop_front();
                    window.evicted = window.evicted.saturating_add(1);
                }
                window.records.push_back(observation);
            }
            // A pre-reset operation cannot repopulate the new window, but its loss remains visible.
            Ok(_) | Err(_) => {
                let _ = self
                    .0
                    .dropped
                    .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| {
                        Some(n.saturating_add(1))
                    });
            }
        }
    }

    pub fn reset(&self) -> Result<(), SafeError> {
        let mut window = self.0.window.try_lock().map_err(observation_lock_error)?;
        window.generation = window.generation.checked_add(1).ok_or(SafeError::Storage)?;
        window.started_at_ms = now_ms();
        window.records.clear();
        window.evicted = 0;
        window.ops_records.clear();
        window.ops_evicted = 0;
        self.0
            .generation
            .store(window.generation, Ordering::Release);
        Ok(())
    }

    pub fn summary(&self) -> Result<SessionSummary, SafeError> {
        let window = self.0.window.try_lock().map_err(observation_lock_error)?;
        let recent: Vec<_> = window.records.iter().cloned().collect();
        let started_at_ms = window.started_at_ms;
        let evicted = window.evicted;
        let installer_ops = OpsSessionSummary {
            capacity: ops::OPS_OBSERVATION_LIMIT,
            evicted: window.ops_evicted,
            dropped_since_start: self.0.ops_dropped.load(Ordering::Relaxed),
            recent: window.ops_records.iter().cloned().collect(),
        };
        drop(window);
        let mut groups = BTreeMap::<_, OperationSummary>::new();
        for record in &recent {
            let group = groups
                .entry((record.unit, record.operation))
                .or_insert_with(|| OperationSummary {
                    unit: record.unit,
                    operation: record.operation,
                    outcomes: OutcomeCounts::default(),
                    sample_count: 0,
                    min_duration_ms: record.duration_ms,
                    mean_duration_ms: 0.0,
                    max_duration_ms: record.duration_ms,
                });
            match record.outcome {
                ObservationOutcome::Success => group.outcomes.success += 1,
                ObservationOutcome::Failure => group.outcomes.failure += 1,
                ObservationOutcome::Cancelled => group.outcomes.cancelled += 1,
                ObservationOutcome::Unknown => group.outcomes.unknown += 1,
            }
            group.sample_count += 1;
            group.min_duration_ms = group.min_duration_ms.min(record.duration_ms);
            group.max_duration_ms = group.max_duration_ms.max(record.duration_ms);
            group.mean_duration_ms +=
                (record.duration_ms - group.mean_duration_ms) / f64::from(group.sample_count);
        }
        Ok(SessionSummary {
            schema_version: 2,
            session_started_at_ms: self.0.session_started_at_ms,
            window_started_at_ms: started_at_ms,
            generated_at_ms: now_ms(),
            capacity: SESSION_OBSERVATION_LIMIT,
            evicted,
            dropped_since_start: self.0.dropped.load(Ordering::Relaxed),
            coverage: SessionCoverage {
                desktop_session_only: true,
                persistence_evidence: false,
                http_operations: vec![
                    ObservedOperation::GetLiveness,
                    ObservedOperation::GetServerInfo,
                    ObservedOperation::GetReadiness,
                    ObservedOperation::GetCapabilities,
                    ObservedOperation::GetAccessPrincipal,
                    ObservedOperation::ListScopes,
                    ObservedOperation::GetScope,
                    ObservedOperation::GetDefaultScope,
                    ObservedOperation::RememberMemory,
                    ObservedOperation::SearchMemory,
                    ObservedOperation::GetArtifactRevision,
                ],
                user_operations: vec![
                    ObservedOperation::ListScopes,
                    ObservedOperation::GetScope,
                    ObservedOperation::GetDefaultScope,
                    ObservedOperation::RememberMemory,
                    ObservedOperation::SearchMemory,
                    ObservedOperation::GetArtifactRevision,
                ],
                identity_checks_included: true,
                installer_ops: self.0.ops_enabled.load(Ordering::Acquire),
            },
            operations: groups.into_values().collect(),
            recent,
            installer_ops,
        })
    }

    /// Serialize only the native allowlisted projection, never connection or business state.
    pub fn diagnostic_text(&self) -> Result<String, SafeError> {
        serde_json::to_string_pretty(&self.summary()?).map_err(|_| SafeError::InvalidResponse)
    }
}

pub(crate) struct ObservationGuard {
    observations: SessionObservations,
    generation: u64,
    started: Instant,
    unit: ObservationUnit,
    operation: ObservedOperation,
    outcome: ObservationOutcome,
    error_code: Option<SafeError>,
    http_status: Option<u16>,
}
impl ObservationGuard {
    pub(crate) fn http_status(&mut self, status: u16) {
        self.http_status = Some(status);
    }

    pub(crate) fn finish<T>(&mut self, result: &Result<T, ApiFailure>) {
        match result {
            Ok(_) => self.outcome = ObservationOutcome::Success,
            Err(error) => {
                self.outcome = if self.outcome == ObservationOutcome::Unknown {
                    // Only the owning write result can resolve a dispatched write's uncertainty.
                    ObservationOutcome::Unknown
                } else if error.code == SafeError::StaleContext {
                    ObservationOutcome::Cancelled
                } else {
                    ObservationOutcome::Failure
                };
                self.error_code = Some(error.code);
            }
        }
        // Readiness 503 can be a successfully decoded fact, but remains an unsuccessful HTTP response.
        if self.unit == ObservationUnit::HttpRequest
            && self.http_status == Some(503)
            && result.is_ok()
        {
            self.outcome = ObservationOutcome::Failure;
            self.error_code = Some(SafeError::RuntimeNotReady);
        }
    }

    pub(crate) fn write_dispatched(&mut self) {
        self.outcome = ObservationOutcome::Unknown;
    }

    pub(crate) fn write_finished(
        &mut self,
        outcome: ObservationOutcome,
        error_code: Option<SafeError>,
    ) {
        self.outcome = outcome;
        self.error_code = error_code;
    }
}
impl Drop for ObservationGuard {
    fn drop(&mut self) {
        self.observations.record(
            self.generation,
            SessionObservation {
                unit: self.unit,
                operation: self.operation,
                outcome: self.outcome,
                completed_at_ms: now_ms(),
                duration_ms: self.started.elapsed().as_secs_f64() * 1000.0,
                http_status: self.http_status,
                error_code: self.error_code,
            },
        );
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}
fn observation_lock_error<T>(error: TryLockError<T>) -> SafeError {
    match error {
        TryLockError::WouldBlock => SafeError::Busy,
        TryLockError::Poisoned(_) => SafeError::Storage,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::transport::{Endpoint, ServerApi};
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::TcpListener,
        sync::Notify,
    };

    #[tokio::test]
    async fn collector_contention_cannot_fail_an_http_request_and_reports_lost_coverage() {
        let observations = SessionObservations::default();
        let held = observations.clone();
        let (ready, received) = std::sync::mpsc::channel();
        let (release, wait) = std::sync::mpsc::channel();
        let worker = std::thread::spawn(move || {
            let _lock = held.0.window.lock().unwrap();
            ready.send(()).unwrap();
            wait.recv().unwrap();
        });
        received.recv().unwrap();
        assert!(matches!(observations.summary(), Err(SafeError::Busy)));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let api = ServerApi::new(
            Endpoint::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap(),
            None,
            None,
        )
        .unwrap()
        .with_observations(observations.clone());
        let response = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut bytes = [0; 4096];
            let received = socket.read(&mut bytes).await.unwrap();
            assert!(received > 0);
            socket.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 15\r\nConnection: close\r\n\r\n{\"status\":\"ok\"}").await.unwrap();
        });
        assert_eq!(api.live().await.unwrap().status, "ok");
        response.await.unwrap();
        release.send(()).unwrap();
        worker.join().unwrap();
        let summary = observations.summary().unwrap();
        assert!(summary.recent.is_empty());
        assert_eq!(summary.dropped_since_start, 1);
        observations.reset().unwrap();
        assert_eq!(observations.summary().unwrap().dropped_since_start, 1);
    }

    #[tokio::test]
    async fn poisoned_collector_cannot_suppress_task_cancellation() {
        let observations = SessionObservations::default();
        let poisoned = observations.clone();
        let _ = std::thread::spawn(move || {
            let _lock = poisoned.0.window.lock().unwrap();
            panic!("synthetic collector failure");
        })
        .join();
        assert!(matches!(observations.summary(), Err(SafeError::Storage)));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let api = ServerApi::new(
            Endpoint::parse(&format!("http://{}", listener.local_addr().unwrap())).unwrap(),
            None,
            None,
        )
        .unwrap()
        .with_observations(observations.clone());
        let accepted = Arc::new(Notify::new());
        let notify = accepted.clone();
        let response = tokio::spawn(async move {
            let (_socket, _) = listener.accept().await.unwrap();
            notify.notify_one();
            std::future::pending::<()>().await;
        });
        let request = tokio::spawn(async move { api.live().await });
        tokio::time::timeout(std::time::Duration::from_secs(3), accepted.notified())
            .await
            .unwrap();
        request.abort();
        assert!(request.await.err().unwrap().is_cancelled());
        response.abort();
        assert_eq!(observations.0.dropped.load(Ordering::Relaxed), 1);
    }
}
