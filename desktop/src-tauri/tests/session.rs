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
    credentials::WindowsVault,
    error::SafeError,
};
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::Notify,
};
struct Fixture {
    endpoint: String,
    scope_started: Arc<Notify>,
    release: Arc<Notify>,
    changed_identity: Arc<AtomicBool>,
    task: tokio::task::JoinHandle<()>,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.task.abort();
        self.release.notify_waiters();
    }
}
async fn fixture() -> Fixture {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let scope_started = Arc::new(Notify::new());
    let release = Arc::new(Notify::new());
    let changed_identity = Arc::new(AtomicBool::new(false));
    let (started, ready, changed) = (
        scope_started.clone(),
        release.clone(),
        changed_identity.clone(),
    );
    let task = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            let (started, ready, changed) = (started.clone(), ready.clone(), changed.clone());
            tokio::spawn(async move {
                let mut request = vec![];
                loop {
                    let mut buf = [0; 1024];
                    let n = socket.read(&mut buf).await.unwrap();
                    if n == 0 {
                        return;
                    }
                    request.extend_from_slice(&buf[..n]);
                    if request.windows(4).any(|v| v == b"\r\n\r\n") {
                        break;
                    }
                }
                let text = String::from_utf8(request).unwrap();
                let path = text.split_whitespace().nth(1).unwrap();
                let (status, body) = if path.contains("/access/me") {
                    if changed.load(Ordering::Relaxed) {
                        (401, r#"{}"#)
                    } else {
                        (503, r#"{"error":{"code":"runtime_not_ready"}}"#)
                    }
                } else if path.contains("/ready") {
                    (
                        200,
                        r#"{"status":"ready","checks":{"access_mode":"disabled"}}"#,
                    )
                } else if path.contains("/capabilities") {
                    (403, r#"{}"#)
                } else if path.contains("/scopes?") {
                    started.notify_one();
                    ready.notified().await;
                    (200, r#"{"items":[],"next_cursor":null}"#)
                } else {
                    (200, r#"{"status":"ok"}"#)
                };
                let response = format!(
                    "HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = socket.write_all(response.as_bytes()).await;
            });
        }
    });
    Fixture {
        endpoint,
        scope_started,
        release,
        changed_identity,
        task,
    }
}
fn add(manager: &ConnectionManager, name: &str, endpoint: &str) -> String {
    let compatibility = manager.state().unwrap().compatibility_profiles[0]
        .id
        .clone();
    let state = manager.save_profile(serde_json::from_value(serde_json::json!({
        "id":null,"revision":null,"name":name,"endpoint":endpoint,
        "authentication":"unauthenticated_loopback","caPem":null,"compatibility":compatibility,
        "keepCredential":false,"credential":null
    })).unwrap()).unwrap();
    state
        .profiles
        .iter()
        .find(|p| p.name == name)
        .unwrap()
        .id
        .clone()
}
fn manager(dir: &tempfile::TempDir) -> Arc<ConnectionManager> {
    Arc::new(ConnectionManager::new(
        ProfileRepository::open(dir.path().join("profiles.json"), Arc::new(WindowsVault)).unwrap(),
    ))
}
#[tokio::test]
async fn checking_another_profile_does_not_activate_it_and_capability_denial_is_independent() {
    let server = fixture().await;
    let dir = tempfile::tempdir().unwrap();
    let manager = manager(&dir);
    let a = add(&manager, "A", &server.endpoint);
    let b = add(&manager, "B", &server.endpoint);
    manager.check(&a, true).await.unwrap();
    let state = manager.check(&b, false).await.unwrap();
    let active = state.active.unwrap();
    assert_eq!(active.connection_id, a);
    assert!(active.report.anonymous_access);
    assert!(active.report.compatibility_verified);
    assert_eq!(
        active.report.capabilities.error.unwrap().code,
        SafeError::Forbidden
    );
    server.release.notify_one();
    assert!(manager.scopes(active.generation, "", None).await.is_ok());
}
#[tokio::test]
async fn switching_connection_cancels_a_slow_scope_read_before_it_can_return_old_data() {
    let server = fixture().await;
    let dir = tempfile::tempdir().unwrap();
    let manager = manager(&dir);
    let a = add(&manager, "A", &server.endpoint);
    let b = add(&manager, "B", &server.endpoint);
    let generation = manager.check(&a, true).await.unwrap().generation;
    let reader = manager.clone();
    let read = tokio::spawn(async move { reader.scopes(generation, "", None).await });
    tokio::time::timeout(
        std::time::Duration::from_secs(3),
        server.scope_started.notified(),
    )
    .await
    .unwrap();
    manager.check(&b, true).await.unwrap();
    let result = tokio::time::timeout(std::time::Duration::from_secs(1), read)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.err().unwrap().code, SafeError::StaleContext);
    assert_eq!(manager.state().unwrap().active.unwrap().connection_id, b);
    server.release.notify_one();
}
#[tokio::test]
async fn changed_authentication_drops_the_active_context_before_scope_access() {
    let server = fixture().await;
    let dir = tempfile::tempdir().unwrap();
    let manager = manager(&dir);
    let id = add(&manager, "A", &server.endpoint);
    let generation = manager.check(&id, true).await.unwrap().generation;
    server.changed_identity.store(true, Ordering::Relaxed);
    assert_eq!(
        manager
            .scopes(generation, "", None)
            .await
            .err()
            .unwrap()
            .code,
        SafeError::Unauthorized
    );
    assert!(manager.state().unwrap().active.is_none());
}

#[tokio::test]
async fn editing_a_scope_query_cancels_its_read_without_disconnecting() {
    let server = fixture().await;
    let dir = tempfile::tempdir().unwrap();
    let manager = manager(&dir);
    let id = add(&manager, "A", &server.endpoint);
    let generation = manager.check(&id, true).await.unwrap().generation;
    let reader = manager.clone();
    let read = tokio::spawn(async move { reader.scopes(generation, "old", None).await });
    tokio::time::timeout(
        std::time::Duration::from_secs(3),
        server.scope_started.notified(),
    )
    .await
    .unwrap();
    manager.cancel_scope_reads(generation).unwrap();
    let result = tokio::time::timeout(std::time::Duration::from_secs(1), read)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(result.err().unwrap().code, SafeError::StaleContext);
    assert_eq!(manager.state().unwrap().active.unwrap().connection_id, id);
    server.release.notify_one();
}
