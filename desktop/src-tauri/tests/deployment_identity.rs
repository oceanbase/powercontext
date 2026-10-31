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
    connections::{
        profiles::ProfileRepository,
        session::{ConnectionManager, WriteStatus},
    },
    credentials::NativeVault,
    error::SafeError,
    transport::wire::ArtifactReference,
};
use serde_json::{Value, json};
use std::sync::{Arc, Mutex};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::Notify,
    time::{Duration, timeout},
};

struct State {
    discovery: (u16, Value),
    principal: Option<String>,
    pause: Option<String>,
    requests: Vec<String>,
}
struct Fixture {
    manager: Arc<ConnectionManager>,
    id: String,
    state: Arc<Mutex<State>>,
    started: Arc<Notify>,
    release: Arc<Notify>,
    server: tokio::task::JoinHandle<()>,
    _directory: tempfile::TempDir,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.server.abort();
        self.release.notify_waiters();
    }
}
fn discovery(id: &str) -> Value {
    json!({"schema_version":{"major":1,"minor":0},"product":"powercontext","server_id":id,
        "package_version":"synthetic","api_contract_version":{"major":1,"minor":4},"feature_contracts":{
        "access.principal":{"version":{"major":1,"minor":0},"operations":["get_access_principal"]},
        "scope.selection":{"version":{"major":1,"minor":0},"operations":["list_scopes","get_scope","get_default_scope"]},
        "memory.explicit":{"version":{"major":1,"minor":0},"operations":["remember_memory","search_memory"]}}})
}
fn scope() -> Value {
    json!({"scope_id":"scope-a","title":"Private scope","summary":"","context_references":[],
        "external_references":[],"version":1})
}
fn reference() -> ArtifactReference {
    serde_json::from_value(json!({"family":"atomic-memory","artifact_id":"atomic-a","revision":1}))
        .unwrap()
}
fn record() -> Value {
    json!({"artifact":reference(),"kind":"note","text":"private result","state":"active",
        "state_version":1,"merged_into_id":null})
}
impl Fixture {
    async fn new(authenticated: bool) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("http://{}", listener.local_addr().unwrap());
        let state = Arc::new(Mutex::new(State {
            discovery: (200, discovery("server-a")),
            principal: authenticated.then(|| "same-principal".into()),
            pause: None,
            requests: vec![],
        }));
        let started = Arc::new(Notify::new());
        let release = Arc::new(Notify::new());
        let (responses, entered, resume) = (state.clone(), started.clone(), release.clone());
        let server = tokio::spawn(async move {
            loop {
                let (mut socket, _) = listener.accept().await.unwrap();
                let (responses, entered, resume) =
                    (responses.clone(), entered.clone(), resume.clone());
                tokio::spawn(async move {
                    let mut bytes = vec![];
                    loop {
                        let mut chunk = [0; 4096];
                        let count = socket.read(&mut chunk).await.unwrap();
                        if count == 0 {
                            return;
                        }
                        bytes.extend_from_slice(&chunk[..count]);
                        if let Some(end) = bytes.windows(4).position(|v| v == b"\r\n\r\n") {
                            let headers = String::from_utf8_lossy(&bytes[..end]);
                            let length = headers
                                .lines()
                                .find_map(|line| {
                                    line.to_ascii_lowercase()
                                        .strip_prefix("content-length: ")
                                        .map(|value| value.parse::<usize>().unwrap())
                                })
                                .unwrap_or(0);
                            if bytes.len() >= end + 4 + length {
                                break;
                            }
                        }
                    }
                    let path = String::from_utf8_lossy(&bytes)
                        .split_whitespace()
                        .nth(1)
                        .unwrap()
                        .to_owned();
                    let (status, value, pause) = {
                        let mut state = responses.lock().unwrap();
                        state.requests.push(path.clone());
                        let (status, value) = match path.as_str() {
                            "/v1/server-info" => state.discovery.clone(),
                            "/v1/access/me" => match &state.principal {
                                Some(id) => (
                                    200,
                                    json!({"principal":{"type":"service","id":id},"mode":"enforced",
                                    "resource_kinds":[],"artifact_families":[],"provider_capabilities":{
                                    "safe_resource_filtering":true,"multi_requirement_check":true,"relationship_management":false,
                                    "group_subjects":false,"multi_principal":false,"max_direct_resource_keys":100}}),
                                ),
                                None => (503, json!({"error":{"code":"runtime_not_ready"}})),
                            },
                            "/health/ready" => (
                                200,
                                json!({"status":"ready","checks":{
                                "access_mode":if state.principal.is_some() {"enforced"} else {"disabled"}}}),
                            ),
                            "/v1/capabilities" => (403, json!({})),
                            "/v1/scopes/default" | "/v1/scopes/scope-a" => (200, scope()),
                            path if path.starts_with("/v1/scopes?") => {
                                (200, json!({"items":[scope()],"next_cursor":"old-cursor"}))
                            }
                            "/v1/memory/search" => (
                                200,
                                json!({"mode":"text","hits":[{"memory":record(),"score":1,"matched_by":["text"]}]}),
                            ),
                            "/v1/memory/remember" => {
                                (200, json!({"changed":true,"records":[record()]}))
                            }
                            path if path.contains("/revisions/") => (
                                200,
                                json!({"scope_id":"scope-a","family":"atomic-memory",
                                "artifact_id":"atomic-a","revision":1,"content":{"schema":"powercontext.atomic-memory.v1",
                                "kind":"note","text":"private result"},"sources":[],"artifacts":[],
                                "content_digest":format!("sha256:{}", "0".repeat(64))}),
                            ),
                            _ => (200, json!({"status":"ok"})),
                        };
                        (
                            status,
                            value,
                            state
                                .pause
                                .as_ref()
                                .is_some_and(|prefix| path.starts_with(prefix)),
                        )
                    };
                    if pause {
                        entered.notify_one();
                        resume.notified().await;
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
        let directory = tempfile::tempdir().unwrap();
        let manager = Arc::new(common::connection_manager(
            ProfileRepository::open(
                directory.path().join("profiles.json"),
                Arc::new(NativeVault),
            )
            .unwrap(),
        ));
        let saved = manager.save_profile(serde_json::from_value(json!({"id":null,"revision":null,"name":"Synthetic",
            "endpoint":endpoint,"authentication":if authenticated {"bearer"} else {"unauthenticated_loopback"},
            "caPem":null,"compatibility":common::PROFILE_ID,"keepCredential":false,
            "credential":if authenticated {json!({"secret":"synthetic-token","storage":"session_only"})} else {Value::Null}})).unwrap()).unwrap();
        Self {
            manager,
            id: saved.profiles[0].id.clone(),
            state,
            started,
            release,
            server,
            _directory: directory,
        }
    }
    async fn activate(&self) -> u32 {
        let state = self.manager.check(&self.id, true).await.unwrap();
        assert!(state.active.unwrap().report.compatibility_verified);
        state.generation
    }
    async fn select(&self) -> u32 {
        self.manager
            .select_scope(self.activate().await, "scope-a")
            .await
            .unwrap()
            .generation
    }
    fn replace(&self) {
        self.state.lock().unwrap().discovery = (200, discovery("server-b"));
    }
    async fn entered(&self) {
        timeout(Duration::from_secs(5), self.started.notified())
            .await
            .unwrap();
    }
    fn assert_disconnected(&self, previous: u32) {
        let state = self.manager.state().unwrap();
        assert!(state.active.is_none());
        assert!(state.generation > previous);
        assert!(
            state
                .reports
                .iter()
                .all(|report| report.connection_id != self.id)
        );
    }
}

#[tokio::test]
async fn replacement_with_the_same_principal_blocks_reads_and_writes_before_dispatch() {
    for authenticated in [false, true] {
        for write in [false, true] {
            let fixture = Fixture::new(authenticated).await;
            let generation = fixture.select().await;
            fixture.state.lock().unwrap().requests.clear();
            fixture.replace();
            let error = if write {
                fixture
                    .manager
                    .remember(generation, "must not reach server-b")
                    .await
                    .map(|_| ())
            } else {
                fixture
                    .manager
                    .scopes(generation, "", Some("old-cursor"))
                    .await
                    .map(|_| ())
            }
            .err()
            .unwrap();
            assert_eq!(error.code, SafeError::StaleContext);
            fixture.assert_disconnected(generation);
            assert!(fixture.manager.state().unwrap().last_write.is_none());
            assert!(
                !fixture
                    .state
                    .lock()
                    .unwrap()
                    .requests
                    .iter()
                    .any(|path| path.contains("/memory/") || path.starts_with("/v1/scopes"))
            );
            assert!(
                fixture
                    .manager
                    .scopes(generation, "", Some("old-cursor"))
                    .await
                    .is_err()
            );
            // Only an explicit check can establish the replacement's context.
            let generation = fixture.activate().await;
            assert!(
                fixture
                    .manager
                    .state()
                    .unwrap()
                    .active
                    .unwrap()
                    .scope
                    .is_none()
            );
            assert!(fixture.manager.scopes(generation, "", None).await.is_ok());
        }
    }
}

#[tokio::test]
async fn late_private_reads_are_rejected_when_the_deployment_changes_during_the_request() {
    for path in [
        "/v1/scopes?",
        "/v1/scopes/default",
        "/v1/scopes/scope-a",
        "/v1/memory/search",
        "/v1/scopes/scope-a/artifacts/",
    ] {
        let fixture = Fixture::new(true).await;
        let generation = fixture.select().await;
        fixture.state.lock().unwrap().pause = Some(path.into());
        let manager = fixture.manager.clone();
        let read = tokio::spawn(async move {
            match path {
                "/v1/scopes?" => manager.scopes(generation, "", None).await.map(|_| ()),
                "/v1/scopes/default" => manager.default_scope(generation).await.map(|_| ()),
                "/v1/scopes/scope-a" => manager
                    .select_scope(generation, "scope-a")
                    .await
                    .map(|_| ()),
                "/v1/memory/search" => manager
                    .search_memory(generation, "private")
                    .await
                    .map(|_| ()),
                _ => manager
                    .atomic_memory_entry(generation, &reference())
                    .await
                    .map(|_| ()),
            }
        });
        fixture.entered().await;
        fixture.replace();
        fixture.release.notify_one();
        let error = timeout(Duration::from_secs(5), read)
            .await
            .unwrap()
            .unwrap()
            .err()
            .unwrap();
        assert_eq!(error.code, SafeError::StaleContext, "{path}");
        fixture.assert_disconnected(generation);
    }
}

#[tokio::test]
async fn failed_discovery_cannot_downgrade_an_active_deployment_to_legacy() {
    for status in [404, 401, 403, 503] {
        let fixture = Fixture::new(false).await;
        let generation = fixture.activate().await;
        fixture.state.lock().unwrap().discovery = (status, json!({}));
        assert!(fixture.manager.scopes(generation, "", None).await.is_err());
        fixture.assert_disconnected(generation);
    }
}

#[tokio::test]
async fn a_legacy_session_requires_explicit_recheck_when_discovery_appears() {
    let fixture = Fixture::new(false).await;
    fixture.state.lock().unwrap().discovery = (404, json!({}));
    let generation = fixture.activate().await;
    assert!(fixture.manager.scopes(generation, "", None).await.is_ok());
    fixture.replace();
    assert_eq!(
        fixture
            .manager
            .scopes(generation, "", None)
            .await
            .err()
            .unwrap()
            .code,
        SafeError::StaleContext
    );
    fixture.assert_disconnected(generation);
}

#[tokio::test]
async fn unchanged_identity_allows_the_complete_flow_and_keeps_the_write_target() {
    for authenticated in [false, true] {
        let fixture = Fixture::new(authenticated).await;
        let generation = fixture.select().await;
        // A package upgrade is not a deployment replacement.
        fixture.state.lock().unwrap().discovery.1["package_version"] = json!("new-package");
        assert!(fixture.manager.scopes(generation, "", None).await.is_ok());
        assert_eq!(
            fixture
                .manager
                .default_scope(generation)
                .await
                .unwrap()
                .scope_id,
            "scope-a"
        );
        assert_eq!(
            fixture
                .manager
                .search_memory(generation, "private")
                .await
                .unwrap()
                .hits
                .len(),
            1
        );
        assert_eq!(
            fixture
                .manager
                .atomic_memory_entry(generation, &reference())
                .await
                .unwrap()
                .text,
            "private result"
        );
        let outcome = fixture.manager.remember(generation, "note").await.unwrap();
        assert_eq!(outcome.record.status, WriteStatus::Succeeded);
        assert_eq!(
            serde_json::to_value(&outcome.record.context).unwrap()["serverId"],
            "server-a"
        );
        assert_eq!(outcome.record.references, vec![reference()]);
        assert!(outcome.result.is_some());
        assert_eq!(fixture.manager.state().unwrap().generation, generation);
    }
}

#[tokio::test]
async fn replacement_during_a_write_preserves_an_unknown_original_target_without_replay() {
    let fixture = Fixture::new(true).await;
    let generation = fixture.select().await;
    fixture.state.lock().unwrap().pause = Some("/v1/memory/remember".into());
    let manager = fixture.manager.clone();
    let write =
        tokio::spawn(async move { manager.remember(generation, "private submitted note").await });
    fixture.entered().await;
    fixture.replace();
    fixture.release.notify_one();
    let outcome = timeout(Duration::from_secs(5), write)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(outcome.record.status, WriteStatus::Unknown);
    assert_eq!(outcome.record.error.unwrap().code, SafeError::StaleContext);
    assert!(outcome.result.is_none());
    assert!(outcome.record.references.is_empty());
    let state = fixture.manager.state().unwrap();
    let original = serde_json::to_value(state.last_write.unwrap()).unwrap();
    assert_eq!(original["context"]["serverId"], "server-a");
    assert_eq!(original["context"]["scopeId"], "scope-a");
    assert!(!original.to_string().contains("private submitted note"));
    fixture.assert_disconnected(generation);
    assert_eq!(
        fixture
            .state
            .lock()
            .unwrap()
            .requests
            .iter()
            .filter(|path| *path == "/v1/memory/remember")
            .count(),
        1
    );
}

#[tokio::test]
async fn an_old_write_cannot_disconnect_a_newly_activated_deployment() {
    let fixture = Fixture::new(true).await;
    let generation = fixture.select().await;
    fixture.state.lock().unwrap().pause = Some("/v1/memory/remember".into());
    let manager = fixture.manager.clone();
    let write = tokio::spawn(async move { manager.remember(generation, "note").await });
    fixture.entered().await;
    fixture.replace();
    let replacement = fixture.activate().await;
    fixture.release.notify_one();
    let outcome = timeout(Duration::from_secs(5), write)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(outcome.record.status, WriteStatus::Unknown);
    assert!(outcome.result.is_none());
    let state = fixture.manager.state().unwrap();
    assert_eq!(state.generation, replacement);
    assert_eq!(
        state
            .active
            .unwrap()
            .report
            .discovery
            .unwrap()
            .server
            .unwrap()
            .server_id,
        "server-b"
    );
    assert_eq!(
        serde_json::to_value(state.last_write.unwrap()).unwrap()["context"]["serverId"],
        "server-a"
    );
}

#[tokio::test]
async fn replacement_during_activation_does_not_mix_deployment_and_principal_facts() {
    let fixture = Fixture::new(true).await;
    fixture.state.lock().unwrap().pause = Some("/v1/access/me".into());
    let (manager, id) = (fixture.manager.clone(), fixture.id.clone());
    let check = tokio::spawn(async move { manager.check(&id, true).await });
    fixture.entered().await;
    fixture.replace();
    fixture.release.notify_one();
    let error = timeout(Duration::from_secs(5), check)
        .await
        .unwrap()
        .unwrap()
        .err()
        .unwrap();
    assert_eq!(error.code, SafeError::StaleContext);
    assert!(fixture.manager.state().unwrap().active.is_none());
}

#[tokio::test]
async fn failed_recheck_drops_the_previous_verified_report() {
    for status in [200, 503] {
        let fixture = Fixture::new(true).await;
        fixture.activate().await;
        assert!(!fixture.manager.state().unwrap().reports.is_empty());
        fixture.state.lock().unwrap().pause = Some("/v1/access/me".into());
        let (manager, id) = (fixture.manager.clone(), fixture.id.clone());
        let check = tokio::spawn(async move { manager.check(&id, true).await });
        fixture.entered().await;
        fixture.state.lock().unwrap().discovery = (status, discovery("server-b"));
        fixture.release.notify_one();
        assert!(
            timeout(Duration::from_secs(5), check)
                .await
                .unwrap()
                .unwrap()
                .is_err()
        );
        let state = fixture.manager.state().unwrap();
        assert!(state.active.is_none());
        assert!(
            state
                .reports
                .iter()
                .all(|report| report.connection_id != fixture.id)
        );
    }
}

#[tokio::test]
async fn post_write_discovery_503_disconnects_with_an_unknown_original_receipt() {
    let fixture = Fixture::new(true).await;
    let generation = fixture.select().await;
    fixture.state.lock().unwrap().pause = Some("/v1/memory/remember".into());
    let manager = fixture.manager.clone();
    let write = tokio::spawn(async move { manager.remember(generation, "preserve me").await });
    fixture.entered().await;
    fixture.state.lock().unwrap().discovery = (503, json!({"error":{"code":"runtime_not_ready"}}));
    fixture.release.notify_one();
    let outcome = timeout(Duration::from_secs(5), write)
        .await
        .unwrap()
        .unwrap()
        .unwrap();
    assert_eq!(outcome.record.status, WriteStatus::Unknown);
    assert_eq!(
        outcome.record.error.as_ref().unwrap().code,
        SafeError::RuntimeNotReady
    );
    assert!(outcome.result.is_none());
    assert_eq!(
        outcome.record.context.server_id.as_deref(),
        Some("server-a")
    );
    fixture.assert_disconnected(generation);
    assert_eq!(
        fixture
            .manager
            .state()
            .unwrap()
            .last_write
            .unwrap()
            .operation_id,
        outcome.record.operation_id
    );
}

#[tokio::test]
async fn principal_revocation_while_reading_discards_private_results() {
    let fixture = Fixture::new(true).await;
    let generation = fixture.select().await;
    fixture.state.lock().unwrap().pause = Some("/v1/memory/search".into());
    let manager = fixture.manager.clone();
    let read = tokio::spawn(async move { manager.search_memory(generation, "private").await });
    fixture.entered().await;
    fixture.state.lock().unwrap().principal = Some("different-principal".into());
    fixture.release.notify_one();
    assert_eq!(
        timeout(Duration::from_secs(5), read)
            .await
            .unwrap()
            .unwrap()
            .err()
            .unwrap()
            .code,
        SafeError::StaleContext
    );
    fixture.assert_disconnected(generation);
}

#[tokio::test]
async fn local_handoff_fences_default_and_exact_scope_reads_with_the_observed_deployment() {
    for path in ["/v1/scopes/default", "/v1/scopes/scope-a"] {
        let fixture = Fixture::new(false).await;
        let before = fixture.manager.state().unwrap();
        let endpoint = before.profiles[0].endpoint.clone();
        fixture.state.lock().unwrap().pause = Some(path.into());
        let manager = fixture.manager.clone();
        let handoff = tokio::spawn(async move {
            manager
                .connect_local(
                    before.generation,
                    endpoint,
                    common::PROFILE_ID.into(),
                    "synthetic".into(),
                )
                .await
        });
        fixture.entered().await;
        assert!(fixture.manager.state().unwrap().active.is_none());
        fixture.replace();
        fixture.release.notify_one();
        let error = timeout(Duration::from_secs(5), handoff)
            .await
            .unwrap()
            .unwrap()
            .err()
            .unwrap();
        assert_eq!(error.code, SafeError::StaleContext, "{path}");
        fixture.assert_disconnected(before.generation);
        if path == "/v1/scopes/default" {
            assert!(
                !fixture
                    .state
                    .lock()
                    .unwrap()
                    .requests
                    .iter()
                    .any(|request| request == "/v1/scopes/scope-a")
            );
        }
    }
}

#[tokio::test]
async fn local_scope_retention_requires_the_same_deployment_and_rechecks_it_after_the_exact_read() {
    for replace_during_read in [false, true] {
        let fixture = Fixture::new(false).await;
        let before = fixture.manager.state().unwrap();
        let selected = fixture
            .manager
            .connect_local(
                before.generation,
                before.profiles[0].endpoint.clone(),
                common::PROFILE_ID.into(),
                "synthetic".into(),
            )
            .await
            .unwrap();
        let id = selected.active.as_ref().unwrap().connection_id.clone();
        let refreshed = fixture.manager.check(&id, false).await.unwrap();
        assert_eq!(refreshed.active.unwrap().scope.unwrap().scope_id, "scope-a");
        if replace_during_read {
            fixture.state.lock().unwrap().pause = Some("/v1/scopes/scope-a".into());
            let manager = fixture.manager.clone();
            let recheck = tokio::spawn(async move { manager.check(&id, false).await });
            fixture.entered().await;
            fixture.replace();
            fixture.release.notify_one();
            let error = timeout(Duration::from_secs(5), recheck)
                .await
                .unwrap()
                .unwrap()
                .err()
                .unwrap();
            assert_eq!(error.code, SafeError::StaleContext);
            fixture.assert_disconnected(refreshed.generation);
        } else {
            fixture.replace();
            let replaced = fixture.manager.check(&id, false).await.unwrap();
            assert!(replaced.active.unwrap().scope.is_none());
        }
    }
}
