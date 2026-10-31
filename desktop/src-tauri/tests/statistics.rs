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

mod common;

use powercontext_desktop::{
    connections::{profiles::ProfileRepository, session::ConnectionManager},
    credentials::NativeVault,
    error::SafeError,
    session_observations::{ObservationOutcome, ObservationUnit, ObservedOperation},
    transport::{
        ExactRecallTokenStatistics, ExactScopeStatistics, ExactScopeStatisticsItem,
        MAX_RESPONSE_BYTES, wire::*,
    },
};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, AtomicU16, Ordering},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::Notify,
};

fn response() -> serde_json::Value {
    let period = ResolvedUsagePeriod::new(
        StatsPeriod::Today,
        "2026-10-24".into(),
        "2026-10-24".into(),
        Timezone::Utc,
    );
    let usage = UsageStatistics {
        period: period.clone(),
        ..Default::default()
    };
    let recall = ExactRecallTokenStatistics {
        period,
        estimator: None,
        totals: RecallTokenValue::default(),
        daily: vec![],
    };
    serde_json::to_value(ExactScopeStatistics {
        selection: ExactScopeSelection::new(ExactScopeSelectionMode::Exact, vec!["scope-a".into()]),
        scope_ids: vec!["scope-a".into()],
        as_of: "2026-10-24T00:00:00Z".into(),
        inventory: InventoryStatistics::default(),
        usage: usage.clone(),
        recall: recall.clone(),
        by_scope: vec![ExactScopeStatisticsItem {
            scope_id: "scope-a".into(),
            usage,
            recall,
            inventory: InventoryStatistics::default(),
            recurrence: RecurrenceStatistics::default(),
        }],
    })
    .unwrap()
}

struct Fixture {
    manager: Arc<ConnectionManager>,
    body: Arc<Mutex<serde_json::Value>>,
    requests: Arc<Mutex<Vec<serde_json::Value>>>,
    status: Arc<AtomicU16>,
    blocked: Arc<AtomicBool>,
    started: Arc<Notify>,
    release: Arc<Notify>,
    task: tokio::task::JoinHandle<()>,
    _dir: tempfile::TempDir,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.task.abort();
        self.release.notify_waiters();
    }
}
async fn setup() -> Fixture {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let body = Arc::new(Mutex::new(response()));
    let requests = Arc::new(Mutex::new(Vec::new()));
    let status = Arc::new(AtomicU16::new(200));
    let blocked = Arc::new(AtomicBool::new(false));
    let started = Arc::new(Notify::new());
    let release = Arc::new(Notify::new());
    let (bodies, captured, code, block, start, finish) = (
        body.clone(),
        requests.clone(),
        status.clone(),
        blocked.clone(),
        started.clone(),
        release.clone(),
    );
    let task = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            let (bodies, captured, code, block, start, finish) = (
                bodies.clone(),
                captured.clone(),
                code.clone(),
                block.clone(),
                start.clone(),
                finish.clone(),
            );
            tokio::spawn(async move {
                let mut bytes = Vec::new();
                let end = loop {
                    let mut chunk = [0; 4096];
                    let n = socket.read(&mut chunk).await.unwrap_or(0);
                    if n == 0 {
                        return;
                    }
                    bytes.extend_from_slice(&chunk[..n]);
                    if let Some(end) = bytes.windows(4).position(|v| v == b"\r\n\r\n") {
                        let headers = String::from_utf8_lossy(&bytes[..end]);
                        let length = headers
                            .lines()
                            .find_map(|line| {
                                line.to_lowercase()
                                    .strip_prefix("content-length: ")
                                    .map(str::to_owned)
                            })
                            .map(|s| s.parse::<usize>().unwrap())
                            .unwrap_or(0);
                        if bytes.len() >= end + 4 + length {
                            break end;
                        }
                    }
                };
                let request = String::from_utf8_lossy(&bytes[..end])
                    .lines()
                    .next()
                    .unwrap()
                    .to_owned();
                let (status, value) = if request == "POST /v1/stats HTTP/1.1" {
                    captured
                        .lock()
                        .unwrap()
                        .push(serde_json::from_slice(&bytes[end + 4..]).unwrap());
                    start.notify_one();
                    if block.load(Ordering::Relaxed) {
                        finish.notified().await;
                    }
                    (code.load(Ordering::Relaxed), bodies.lock().unwrap().clone())
                } else if request.contains("/v1/server-info ") {
                    (
                        200,
                        serde_json::json!({
                            "schema_version":{"major":1,"minor":0},
                            "product":"powercontext",
                            "server_id":"synthetic-statistics-server",
                            "package_version":"synthetic",
                            "api_contract_version":{"major":1,"minor":4},
                            "feature_contracts":{
                                "scope.selection":{
                                    "version":{"major":1,"minor":0},
                                    "operations":["get_scope","list_scopes","get_default_scope"]
                                }
                            }
                        }),
                    )
                } else if request.contains("/v1/access/me ") {
                    (
                        503,
                        serde_json::json!({"error":{"code":"runtime_not_ready"}}),
                    )
                } else if request.contains("/health/ready ") {
                    (
                        200,
                        serde_json::json!({"status":"ready","checks":{"access_mode":"disabled"}}),
                    )
                } else if request.contains("/v1/capabilities ") {
                    (403, serde_json::json!({}))
                } else if request.contains("/v1/scopes/") {
                    let id = request
                        .split_whitespace()
                        .nth(1)
                        .unwrap()
                        .rsplit('/')
                        .next()
                        .unwrap();
                    (
                        200,
                        serde_json::json!({"scope_id":id,"title":"Synthetic scope","summary":"","context_references":[],"external_references":[],"version":1}),
                    )
                } else {
                    (200, serde_json::json!({"status":"ok"}))
                };
                if status == 0 {
                    return;
                }
                let body = value.to_string();
                let response = format!(
                    "HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = socket.write_all(response.as_bytes()).await;
            });
        }
    });
    let dir = tempfile::tempdir().unwrap();
    let manager = Arc::new(common::connection_manager(
        ProfileRepository::open(dir.path().join("profiles.json"), Arc::new(NativeVault)).unwrap(),
    ));
    let profile = manager.save_profile(serde_json::from_value(serde_json::json!({"id":null,"revision":null,"name":"Statistics fixture","endpoint":endpoint,"authentication":"unauthenticated_loopback","caPem":null,"compatibility":common::PROFILE_ID,"keepCredential":false,"credential":null})).unwrap()).unwrap().profiles[0].id.clone();
    let generation = manager.check(&profile, true).await.unwrap().generation;
    manager.select_scope(generation, "scope-a").await.unwrap();
    Fixture {
        manager,
        body,
        requests,
        status,
        blocked,
        started,
        release,
        task,
        _dir: dir,
    }
}

#[tokio::test]
async fn statistics_use_only_the_current_exact_scope_and_preserve_null_usage() {
    let fixture = setup().await;
    let generation = fixture.manager.state().unwrap().generation;
    let result = fixture
        .manager
        .stats(generation, StatsPeriod::Today)
        .await
        .unwrap();
    assert_eq!(result.scope_ids, ["scope-a"]);
    let summary = fixture.manager.observations().summary().unwrap();
    assert!(
        summary
            .coverage
            .http_operations
            .contains(&ObservedOperation::GetStats)
    );
    assert!(
        summary
            .coverage
            .user_operations
            .contains(&ObservedOperation::GetStats)
    );
    for unit in [ObservationUnit::HttpRequest, ObservationUnit::UserOperation] {
        let records: Vec<_> = summary
            .recent
            .iter()
            .filter(|record| record.operation == ObservedOperation::GetStats && record.unit == unit)
            .collect();
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].outcome, ObservationOutcome::Success);
    }
    assert!(result.usage.totals.generation.input_tokens.is_none());
    assert!(result.recall.estimator.is_none());
    assert!(result.by_scope[0].recall.estimator.is_none());
    assert_eq!(
        fixture.requests.lock().unwrap().as_slice(),
        &[
            serde_json::json!({"selection":{"mode":"exact","scope_ids":["scope-a"]},"period":"today"})
        ]
    );
}

#[tokio::test]
async fn denied_statistics_never_retry_with_broader_selection() {
    for (status, expected) in [
        (401, SafeError::Unauthorized),
        (403, SafeError::Forbidden),
        (0, SafeError::Network),
    ] {
        let fixture = setup().await;
        fixture.status.store(status, Ordering::Relaxed);
        let generation = fixture.manager.state().unwrap().generation;
        assert_eq!(
            fixture
                .manager
                .stats(generation, StatsPeriod::Today)
                .await
                .unwrap_err()
                .code,
            expected
        );
        assert_eq!(fixture.requests.lock().unwrap().len(), 1);
        let summary = fixture.manager.observations().summary().unwrap();
        for unit in [ObservationUnit::HttpRequest, ObservationUnit::UserOperation] {
            let records: Vec<_> = summary
                .recent
                .iter()
                .filter(|record| {
                    record.operation == ObservedOperation::GetStats && record.unit == unit
                })
                .collect();
            assert_eq!(records.len(), 1);
            assert_eq!(records[0].outcome, ObservationOutcome::Failure);
        }
    }
}

#[tokio::test]
async fn rejects_wrong_selection_scope_period_and_oversized_content() {
    for (field, value, expected) in [
        (
            "scope_ids",
            serde_json::json!(["scope-b"]),
            SafeError::InvalidResponse,
        ),
        (
            "selection",
            serde_json::json!({"mode":"all"}),
            SafeError::InvalidResponse,
        ),
        (
            "by_scope",
            serde_json::json!([]),
            SafeError::InvalidResponse,
        ),
        (
            "usage",
            {
                let mut usage = response()["usage"].clone();
                usage["period"]["preset"] = "7d".into();
                usage
            },
            SafeError::InvalidResponse,
        ),
        (
            "as_of",
            serde_json::json!("x".repeat(MAX_RESPONSE_BYTES)),
            SafeError::ResponseTooLarge,
        ),
    ] {
        let fixture = setup().await;
        fixture.body.lock().unwrap()[field] = value;
        let generation = fixture.manager.state().unwrap().generation;
        assert_eq!(
            fixture
                .manager
                .stats(generation, StatsPeriod::Today)
                .await
                .unwrap_err()
                .code,
            expected
        );
    }
}

#[tokio::test]
async fn cancelled_or_changed_context_discards_pending_statistics() {
    for change_scope in [false, true] {
        let fixture = setup().await;
        fixture.blocked.store(true, Ordering::Relaxed);
        let generation = fixture.manager.state().unwrap().generation;
        let manager = fixture.manager.clone();
        let pending =
            tokio::spawn(async move { manager.stats(generation, StatsPeriod::Today).await });
        fixture.started.notified().await;
        if change_scope {
            fixture
                .manager
                .select_scope(generation, "scope-b")
                .await
                .unwrap();
        } else {
            fixture.manager.cancel_stats_reads(generation).unwrap();
        }
        let result = tokio::time::timeout(std::time::Duration::from_secs(3), pending)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(result.unwrap_err().code, SafeError::StaleContext);
        let summary = fixture.manager.observations().summary().unwrap();
        for unit in [ObservationUnit::HttpRequest, ObservationUnit::UserOperation] {
            let records: Vec<_> = summary
                .recent
                .iter()
                .filter(|record| {
                    record.operation == ObservedOperation::GetStats && record.unit == unit
                })
                .collect();
            assert_eq!(records.len(), 1);
            assert_eq!(records[0].outcome, ObservationOutcome::Cancelled);
        }
    }
}

#[tokio::test]
async fn memory_cancellation_does_not_cancel_statistics() {
    let fixture = setup().await;
    fixture.blocked.store(true, Ordering::Relaxed);
    let generation = fixture.manager.state().unwrap().generation;
    let manager = fixture.manager.clone();
    let pending = tokio::spawn(async move { manager.stats(generation, StatsPeriod::Today).await });
    fixture.started.notified().await;
    fixture.manager.cancel_memory_reads(generation).unwrap();
    fixture.release.notify_one();
    assert!(pending.await.unwrap().is_ok());
}
