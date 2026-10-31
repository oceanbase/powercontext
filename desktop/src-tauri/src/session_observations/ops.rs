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

//! A bounded projection of validated Ops replies, independent of request/action latency.
use super::{SessionObservations, now_ms};
use crate::ops_bridge::{
    OpsAction, OpsAttemptPhase, OpsBridgeError, OpsErrorCode, OpsOperationReply, OpsOutcome,
    OpsProgress, OpsServicePhase,
};
use serde::Serialize;
use std::sync::{Mutex, atomic::Ordering};
use ts_rs::TS;

pub(super) const OPS_OBSERVATION_LIMIT: usize = 64;

#[derive(Clone, Debug, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsSessionObservation {
    pub action: OpsAction,
    /// The existing native-generated UUID, supported by ops_operation_status; never a target ID.
    pub operation_id: String,
    pub attempt: Option<u32>,
    pub phase: Option<OpsServicePhase>,
    pub attempt_phase: Option<OpsAttemptPhase>,
    pub outcome: OpsOutcome,
    pub error_code: Option<OpsErrorCode>,
    pub bridge_error: Option<OpsBridgeError>,
    pub observed_at_ms: u64,
}

#[derive(Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OpsSessionSummary {
    pub capacity: usize,
    pub evicted: u64,
    pub dropped_since_start: u64,
    pub recent: Vec<OpsSessionObservation>,
}

pub(crate) struct OpsCallObservation {
    observations: SessionObservations,
    generation: u64,
    progress: Mutex<(Option<u32>, Option<OpsServicePhase>)>,
}

impl OpsCallObservation {
    pub(crate) fn new(observations: SessionObservations) -> Self {
        Self {
            generation: observations.0.generation.load(Ordering::Acquire),
            observations,
            progress: Mutex::new((None, None)),
        }
    }

    pub(crate) fn progress(&self, progress: &OpsProgress) {
        // Only validated enum values enter this snapshot, never the request ID or output line.
        if let Ok(mut latest) = self.progress.try_lock() {
            latest.0 = Some(progress.attempt);
            if progress.phase.is_some() {
                latest.1 = progress.phase;
            }
        }
    }

    pub(crate) fn finish(&self, action: OpsAction, reply: &OpsOperationReply) {
        let dropped = &self.observations.0.ops_dropped;
        // References allocated by the owning bridge are canonical UUIDs. Reject other strings
        // rather than allowing arbitrary identifiers to become diagnostic text.
        let valid_reference = uuid::Uuid::parse_str(&reply.operation_id)
            .is_ok_and(|id| id.to_string() == reply.operation_id);
        if valid_reference
            && let Ok(mut window) = self.observations.0.window.try_lock()
            && window.generation == self.generation
        {
            let progress = self
                .progress
                .try_lock()
                .ok()
                .map(|progress| *progress)
                .unwrap_or_default();
            let phase = reply
                .status
                .as_ref()
                .and_then(|status| {
                    status
                        .result
                        .as_ref()
                        .and_then(|result| result.components.as_ref())
                        .and_then(|components| components.installation_stage)
                })
                .or_else(|| {
                    reply
                        .status
                        .as_ref()
                        .and_then(|status| status.observation.components.as_ref())
                        .and_then(|components| components.installation_stage)
                })
                .or(progress.1);
            let error_code = reply.error.or_else(|| {
                reply
                    .status
                    .as_ref()
                    .and_then(|status| status.result.as_ref())
                    .and_then(|result| result.error)
            });
            if window.ops_records.len() == OPS_OBSERVATION_LIMIT {
                window.ops_records.pop_front();
                window.ops_evicted = window.ops_evicted.saturating_add(1);
            }
            window.ops_records.push_back(OpsSessionObservation {
                action,
                operation_id: reply.operation_id.clone(),
                attempt: reply.attempt.or(progress.0),
                phase,
                attempt_phase: reply.status.as_ref().map(|status| status.phase),
                outcome: reply.outcome,
                error_code,
                bridge_error: reply.bridge_error,
                observed_at_ms: now_ms(),
            });
            return;
        }
        let _ = dropped.fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| {
            Some(n.saturating_add(1))
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn reply(outcome: OpsOutcome) -> OpsOperationReply {
        OpsOperationReply {
            operation_id: uuid::Uuid::new_v4().to_string(),
            outcome,
            attempt: Some(1),
            exit_code: None,
            error: None,
            bridge_error: None,
            status: None,
        }
    }

    #[test]
    fn safe_ops_diagnostics_preserve_owned_results_and_exclude_private_state() {
        let observations = SessionObservations::default();
        crate::ops_bridge::OpsHost::new(None, None, None).with_observations(observations.clone());
        let mut reply = reply(OpsOutcome::Failed);
        reply.status = Some(serde_json::from_value(json!({
            "operationId": reply.operation_id, "attempt": 1, "phase": "finished",
            "executor": {"pid": 123, "observation": "exited"}, "executionState": "finished",
            "outcome": "failed", "result": {"outcome": "failed", "error": "resource_prepare_failed",
                "components": {"endpoint": "https://private-endpoint/?secret=private-token",
                    "installationId": "private-installation-id", "installationStage": "publishing"}},
            "observation": {"components": {"endpoint": "https://private-observation/"}},
            "history": [], "recoveryActions": []
        })).unwrap());
        let original = serde_json::to_value(&reply).unwrap();
        let capture = OpsCallObservation::new(observations.clone());
        capture.progress(&OpsProgress {
            request_id: "private-request-id".into(),
            operation_id: reply.operation_id.clone(),
            attempt: 1,
            sequence: Some(1),
            phase: Some(OpsServicePhase::Preparing),
            accepted: false,
        });
        capture.finish(OpsAction::Apply, &reply);
        assert_eq!(serde_json::to_value(&reply).unwrap(), original);
        let summary = observations.summary().unwrap();
        assert!(summary.coverage.installer_ops);
        assert!(summary.operations.is_empty() && summary.recent.is_empty());
        let record = &summary.installer_ops.recent[0];
        assert_eq!(record.operation_id, reply.operation_id);
        assert_eq!(record.phase, Some(OpsServicePhase::Publishing));
        assert_eq!(record.attempt_phase, Some(OpsAttemptPhase::Finished));
        assert_eq!(record.error_code, Some(OpsErrorCode::ResourcePrepareFailed));
        let text = observations.diagnostic_text().unwrap();
        assert!(!text.contains("private-"));
        let value: serde_json::Value = serde_json::from_str(&text).unwrap();
        let keys: std::collections::BTreeSet<_> = value["installerOps"]["recent"][0]
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            keys,
            [
                "action",
                "operationId",
                "attempt",
                "phase",
                "attemptPhase",
                "outcome",
                "errorCode",
                "bridgeError",
                "observedAtMs"
            ]
            .into_iter()
            .collect()
        );
    }

    #[test]
    fn ops_outcomes_and_lost_reply_phase_are_not_reinterpreted_as_http_results() {
        let observations = SessionObservations::default();
        for outcome in [
            OpsOutcome::Succeeded,
            OpsOutcome::Failed,
            OpsOutcome::PartialFailure,
            OpsOutcome::Pending,
            OpsOutcome::Cancelled,
            OpsOutcome::Uncertain,
            OpsOutcome::Rejected,
            OpsOutcome::Unknown,
        ] {
            let mut reply = reply(outcome);
            reply.attempt = None;
            reply.bridge_error = Some(OpsBridgeError::Timeout);
            let capture = OpsCallObservation::new(observations.clone());
            capture.progress(&OpsProgress {
                request_id: "not-exported".into(),
                operation_id: reply.operation_id.clone(),
                attempt: 2,
                sequence: Some(3),
                phase: Some(OpsServicePhase::Publishing),
                accepted: false,
            });
            capture.finish(OpsAction::Resume, &reply);
            let summary = observations.summary().unwrap();
            let record = summary.installer_ops.recent.last().unwrap();
            assert_eq!(record.outcome, outcome);
            assert_eq!(record.attempt, Some(2));
            assert_eq!(record.phase, Some(OpsServicePhase::Publishing));
            assert_eq!(record.bridge_error, Some(OpsBridgeError::Timeout));
            assert!(summary.recent.is_empty() && summary.operations.is_empty());
        }
    }

    #[test]
    fn ops_retention_reset_and_failure_isolation_keep_loss_visible() {
        let observations = SessionObservations::default();
        let reply = reply(OpsOutcome::Unknown);
        for _ in 0..OPS_OBSERVATION_LIMIT + 3 {
            OpsCallObservation::new(observations.clone())
                .finish(OpsAction::OperationStatus, &reply);
        }
        let summary = observations.summary().unwrap();
        assert_eq!(summary.installer_ops.capacity, OPS_OBSERVATION_LIMIT);
        assert_eq!(summary.installer_ops.recent.len(), OPS_OBSERVATION_LIMIT);
        assert_eq!(summary.installer_ops.evicted, 3);
        let in_flight = OpsCallObservation::new(observations.clone());
        observations.reset().unwrap();
        in_flight.finish(OpsAction::Apply, &reply);
        let reset = observations.summary().unwrap();
        assert!(reset.installer_ops.recent.is_empty());
        assert_eq!(reset.installer_ops.evicted, 0);
        assert_eq!(reset.session_started_at_ms, summary.session_started_at_ms);
        assert_eq!(reset.installer_ops.dropped_since_start, 1);
        {
            let _held = observations.0.window.lock().unwrap();
            OpsCallObservation::new(observations.clone()).finish(OpsAction::Cancel, &reply);
        }
        let mut invalid = reply.clone();
        invalid.operation_id = "private-path-or-token".into();
        OpsCallObservation::new(observations.clone()).finish(OpsAction::OperationStatus, &invalid);
        observations.reset().unwrap();
        assert_eq!(
            observations
                .summary()
                .unwrap()
                .installer_ops
                .dropped_since_start,
            3
        );
        assert!(
            !observations
                .diagnostic_text()
                .unwrap()
                .contains("private-path-or-token")
        );
        let poisoned = observations.clone();
        let _ = std::thread::spawn(move || {
            let _held = poisoned.0.window.lock().unwrap();
            panic!("synthetic collector failure");
        })
        .join();
        OpsCallObservation::new(observations.clone()).finish(OpsAction::Apply, &reply);
        assert_eq!(observations.0.ops_dropped.load(Ordering::Relaxed), 4);
    }
}
