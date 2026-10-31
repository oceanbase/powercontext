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

use powercontext_desktop::{
    connections::{profiles::ProfileRepository, session::ConnectionManager},
    credentials::NativeVault,
    error::SafeError,
    session_observations::{
        ObservationOutcome, ObservationUnit, ObservedOperation, SESSION_OBSERVATION_LIMIT,
        SessionObservations,
    },
};
use std::sync::Arc;

#[tokio::test]
async fn retention_is_bounded_and_aggregates_only_the_retained_window() {
    let dir = tempfile::tempdir().unwrap();
    let manager = ConnectionManager::new(
        ProfileRepository::open(
            dir.path().join("private-profile-path.json"),
            Arc::new(NativeVault),
        )
        .unwrap(),
    );
    let observations = manager.observations();
    assert!(observations.summary().unwrap().recent.is_empty());
    manager.remember(0, "").await.err().unwrap();
    for _ in 0..SESSION_OBSERVATION_LIMIT + 6 {
        assert_eq!(
            manager
                .search_memory(0, "private query")
                .await
                .err()
                .unwrap()
                .code,
            SafeError::NotConnected
        );
    }
    let summary = observations.summary().unwrap();
    assert_eq!(summary.capacity, SESSION_OBSERVATION_LIMIT);
    assert_eq!(summary.recent.len(), summary.capacity);
    assert_eq!(summary.evicted, 7);
    assert_eq!(summary.operations.len(), 1);
    let group = &summary.operations[0];
    assert_eq!(group.unit, ObservationUnit::UserOperation);
    assert_eq!(group.operation, ObservedOperation::SearchMemory);
    assert_eq!(group.outcomes.failure, SESSION_OBSERVATION_LIMIT as u32);
    assert_eq!(group.sample_count, SESSION_OBSERVATION_LIMIT as u32);
    assert!(group.min_duration_ms >= 0.0);
    assert!(group.mean_duration_ms >= group.min_duration_ms);
    assert!(group.mean_duration_ms <= group.max_duration_ms);
    assert!(summary.recent.iter().all(
        |record| record.outcome == ObservationOutcome::Failure && record.http_status.is_none()
    ));
    let text = observations.diagnostic_text().unwrap();
    assert!(!text.contains("private query"));
    assert!(!text.contains("private-profile-path"));
    assert!(!summary.coverage.installer_ops);
    assert!(summary.coverage.identity_checks_included);
    observations.reset().unwrap();
    let reset = observations.summary().unwrap();
    assert_eq!(reset.session_started_at_ms, summary.session_started_at_ms);
    assert!(reset.window_started_at_ms >= summary.window_started_at_ms);
    assert_eq!(reset.evicted, 0);
    assert!(reset.recent.is_empty());
    assert!(reset.operations.is_empty());
    assert!(
        SessionObservations::default()
            .summary()
            .unwrap()
            .recent
            .is_empty()
    );
}
