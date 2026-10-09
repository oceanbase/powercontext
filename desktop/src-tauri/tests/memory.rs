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
    connections::{
        profiles::ProfileRepository,
        session::{ConnectionManager, WriteStatus},
    },
    credentials::WindowsVault,
    error::SafeError,
};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicU16, Ordering},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::Notify,
};
struct Fixture {
    manager: Arc<ConnectionManager>,
    a: String,
    b: String,
    started: Arc<Notify>,
    release: Arc<Notify>,
    requests: Arc<Mutex<Vec<serde_json::Value>>>,
    fail: Arc<AtomicU16>,
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
    let started = Arc::new(Notify::new());
    let release = Arc::new(Notify::new());
    let fail = Arc::new(AtomicU16::new(200));
    let requests = Arc::new(Mutex::new(vec![]));
    let (start, finish, bodies, failure) = (
        started.clone(),
        release.clone(),
        requests.clone(),
        fail.clone(),
    );
    let task = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            let (start, finish, bodies, failure) = (
                start.clone(),
                finish.clone(),
                bodies.clone(),
                failure.clone(),
            );
            tokio::spawn(async move {
                let mut bytes = vec![];
                let end = loop {
                    let mut chunk = [0; 4096];
                    let n = socket.read(&mut chunk).await.unwrap();
                    if n == 0 {
                        return;
                    }
                    bytes.extend_from_slice(&chunk[..n]);
                    if let Some(end) = bytes.windows(4).position(|p| p == b"\r\n\r\n") {
                        let headers = String::from_utf8_lossy(&bytes[..end]);
                        let length: usize = headers
                            .lines()
                            .find_map(|l| {
                                l.to_lowercase()
                                    .strip_prefix("content-length: ")
                                    .map(str::to_owned)
                            })
                            .map(|v| v.parse().unwrap())
                            .unwrap_or(0);
                        if bytes.len() >= end + 4 + length {
                            break end;
                        }
                    }
                };
                let path = String::from_utf8_lossy(&bytes[..end])
                    .split_whitespace()
                    .nth(1)
                    .unwrap()
                    .to_owned();
                let (status, value) = match path.as_str() {
                    "/v1/access/me" => (
                        503,
                        serde_json::json!({"error":{"code":"runtime_not_ready"}}),
                    ),
                    "/health/ready" => (
                        200,
                        serde_json::json!({"status":"ready","checks":{"access_mode":"disabled"}}),
                    ),
                    "/v1/capabilities" => (403, serde_json::json!({})),
                    "/v1/memory/remember" => {
                        bodies
                            .lock()
                            .unwrap()
                            .push(serde_json::from_slice(&bytes[end + 4..]).unwrap());
                        start.notify_one();
                        finish.notified().await;
                        let status = failure.load(Ordering::Relaxed);
                        if status == 0 {
                            // The fixture accepted the write but the response connection is lost.
                            return;
                        }
                        if status != 200 {
                            (status, serde_json::json!({"detail":"must not leak"}))
                        } else {
                            (
                                200,
                                serde_json::json!({"memory":{"family":"memory","artifact_id":"mem-a","revision":1},"entry":null}),
                            )
                        }
                    }
                    path if path.starts_with("/v1/scopes/") => (
                        200,
                        serde_json::json!({"scope_id":path.rsplit('/').next().unwrap(),"title":"Synthetic scope","summary":"","context_references":[],"external_references":[],"version":1}),
                    ),
                    _ => (200, serde_json::json!({"status":"ok"})),
                };
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
    let manager = Arc::new(ConnectionManager::new(
        ProfileRepository::open(dir.path().join("profiles.json"), Arc::new(WindowsVault)).unwrap(),
    ));
    let compatibility = manager.state().unwrap().compatibility_profiles[0]
        .id
        .clone();
    let mut ids = vec![];
    for name in ["A", "B"] {
        let state = manager.save_profile(serde_json::from_value(serde_json::json!({"id":null,"revision":null,"name":name,"endpoint":endpoint,"authentication":"unauthenticated_loopback","caPem":null,"compatibility":compatibility,"keepCredential":false,"credential":null})).unwrap()).unwrap();
        ids.push(
            state
                .profiles
                .iter()
                .find(|p| p.name == name)
                .unwrap()
                .id
                .clone(),
        );
    }
    let generation = manager.check(&ids[0], true).await.unwrap().generation;
    manager.select_scope(generation, "scope-a").await.unwrap();
    Fixture {
        manager,
        a: ids[0].clone(),
        b: ids[1].clone(),
        started,
        release,
        requests,
        fail,
        task,
        _dir: dir,
    }
}
#[tokio::test]
async fn dispatched_write_keeps_its_original_target_and_rejects_duplicate_clicks() {
    let fixture = setup().await;
    let generation = fixture.manager.state().unwrap().generation;
    let manager = fixture.manager.clone();
    let write =
        tokio::spawn(async move { manager.remember(generation, "中文 synthetic note").await });
    tokio::time::timeout(
        std::time::Duration::from_secs(3),
        fixture.started.notified(),
    )
    .await
    .unwrap();
    assert_eq!(
        fixture
            .manager
            .remember(generation, "duplicate")
            .await
            .err()
            .unwrap()
            .code,
        SafeError::Busy
    );
    fixture.manager.check(&fixture.b, true).await.unwrap();
    fixture.release.notify_one();
    let outcome = write.await.unwrap().unwrap();
    assert_eq!(outcome.record.status, WriteStatus::Succeeded);
    assert_eq!(outcome.record.context.connection_id, fixture.a);
    assert_eq!(outcome.record.context.scope_id, "scope-a");
    assert!(outcome.result.is_none());
    assert!(outcome.record.citation.is_none());
    let requests = fixture.requests.lock().unwrap();
    assert_eq!(
        requests.as_slice(),
        &[serde_json::json!({"scope_id":"scope-a","kind":"note","text":"中文 synthetic note"})]
    );
}
#[tokio::test]
async fn dispatched_server_failure_is_unknown_and_is_never_replayed() {
    let fixture = setup().await;
    fixture.fail.store(500, Ordering::Relaxed);
    fixture.release.notify_one();
    let outcome = fixture
        .manager
        .remember(fixture.manager.state().unwrap().generation, "test note")
        .await
        .unwrap();
    assert_eq!(outcome.record.status, WriteStatus::Unknown);
    assert!(outcome.result.is_none());
    assert_eq!(fixture.requests.lock().unwrap().len(), 1);
    let stored = fixture.manager.state().unwrap().last_write.unwrap();
    assert_eq!(stored.status, WriteStatus::Unknown);
    assert!(
        !serde_json::to_string(&stored)
            .unwrap()
            .contains("test note")
    );
}
#[tokio::test]
async fn nullable_entry_success_does_not_invent_a_citation() {
    let fixture = setup().await;
    fixture.release.notify_one();
    let result = fixture
        .manager
        .remember(fixture.manager.state().unwrap().generation, "test note")
        .await
        .unwrap();
    assert_eq!(result.record.status, WriteStatus::Succeeded);
    assert!(result.record.citation.is_none());
    assert!(result.result.unwrap().entry.is_none());
}

#[tokio::test]
async fn dropping_a_dispatched_write_preserves_unknown_metadata_without_body() {
    let fixture = setup().await;
    let generation = fixture.manager.state().unwrap().generation;
    let manager = fixture.manager.clone();
    let write =
        tokio::spawn(async move { manager.remember(generation, "private synthetic body").await });
    tokio::time::timeout(
        std::time::Duration::from_secs(3),
        fixture.started.notified(),
    )
    .await
    .unwrap();
    write.abort();
    assert!(write.await.err().unwrap().is_cancelled());
    let record = fixture.manager.state().unwrap().last_write.unwrap();
    assert_eq!(record.status, WriteStatus::Unknown);
    assert!(
        !serde_json::to_string(&record)
            .unwrap()
            .contains("private synthetic body")
    );
    fixture.release.notify_one();
}
#[tokio::test]
async fn absent_scope_never_uses_the_server_default_for_writing() {
    let fixture = setup().await;
    let generation = fixture
        .manager
        .check(&fixture.a, true)
        .await
        .unwrap()
        .generation;
    assert_eq!(
        fixture
            .manager
            .remember(generation, "no scope")
            .await
            .err()
            .unwrap()
            .code,
        SafeError::ScopeRequired
    );
    assert!(fixture.requests.lock().unwrap().is_empty());
}

#[tokio::test]
async fn explicit_write_rejections_are_failures_without_replay() {
    for (status, code) in [(409, SafeError::Conflict), (422, SafeError::InvalidInput)] {
        let fixture = setup().await;
        fixture.fail.store(status, Ordering::Relaxed);
        fixture.release.notify_one();
        let result = fixture
            .manager
            .remember(
                fixture.manager.state().unwrap().generation,
                "synthetic rejected",
            )
            .await
            .unwrap();
        assert_eq!(result.record.status, WriteStatus::Failed);
        assert_eq!(result.record.error.unwrap().code, code);
        assert!(result.result.is_none());
        assert_eq!(fixture.requests.lock().unwrap().len(), 1);
    }
}

#[tokio::test]
async fn accepted_write_with_lost_response_remains_unknown_without_replay() {
    let fixture = setup().await;
    fixture.fail.store(0, Ordering::Relaxed);
    fixture.release.notify_one();
    let result = fixture
        .manager
        .remember(
            fixture.manager.state().unwrap().generation,
            "synthetic accepted",
        )
        .await
        .unwrap();
    assert_eq!(result.record.status, WriteStatus::Unknown);
    assert!(result.result.is_none());
    let accepted = fixture.requests.lock().unwrap();
    assert_eq!(accepted.len(), 1);
    assert_eq!(accepted[0]["text"], "synthetic accepted");
}
