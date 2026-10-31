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
        discovery::{CompatibilityReason, CompatibilityStatus, DiscoveryResult, DiscoveryStatus},
        profiles::ProfileRepository,
        session::ConnectionManager,
    },
    credentials::NativeVault,
    error::SafeError,
};
use serde_json::{Value, json};
use std::sync::{
    Arc,
    atomic::{AtomicBool, AtomicU16, Ordering},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::Notify,
};

fn document() -> Value {
    json!({"schema_version":{"major":1,"minor":0},"product":"powercontext","server_id":"synthetic-server",
    "package_version":"display-only-version","api_contract_version":{"major":1,"minor":4},"feature_contracts":{
        "access.principal":{"version":{"major":1,"minor":0},"operations":["get_access_principal"]},
        "scope.selection":{"version":{"major":1,"minor":0},"operations":["list_scopes","get_scope","get_default_scope"]},
        "memory.explicit":{"version":{"major":1,"minor":0},"operations":["remember_memory","search_memory"]}
    }})
}
struct Fixture {
    manager: ConnectionManager,
    id: String,
    status: Arc<AtomicU16>,
    scope_status: Arc<AtomicU16>,
    hold_scope: Arc<AtomicBool>,
    change_access_on_exact: Arc<AtomicBool>,
    scope_started: Arc<Notify>,
    scope_release: Arc<Notify>,
    server: tokio::task::JoinHandle<()>,
    _directory: tempfile::TempDir,
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.server.abort();
    }
}
async fn fixture(status: u16, document: Value, qualified: bool) -> Fixture {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/prefix", listener.local_addr().unwrap());
    let status = Arc::new(AtomicU16::new(status));
    let response_status = status.clone();
    let scope_status = Arc::new(AtomicU16::new(200));
    let hold_scope = Arc::new(AtomicBool::new(false));
    let change_access_on_exact = Arc::new(AtomicBool::new(false));
    let access_enabled = Arc::new(AtomicBool::new(false));
    let change_access = change_access_on_exact.clone();
    let scope_started = Arc::new(Notify::new());
    let scope_release = Arc::new(Notify::new());
    let (scope_reply, scope_hold, started, release) = (
        scope_status.clone(),
        hold_scope.clone(),
        scope_started.clone(),
        scope_release.clone(),
    );
    let server = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            let document = document.clone();
            let status = response_status.load(Ordering::Relaxed);
            let (scope_status, hold_scope, scope_started, scope_release) = (
                scope_reply.clone(),
                scope_hold.clone(),
                started.clone(),
                release.clone(),
            );
            let change_access = change_access.clone();
            let access_enabled = access_enabled.clone();
            tokio::spawn(async move {
                let mut bytes = Vec::new();
                loop {
                    let mut chunk = [0; 4096];
                    let count = socket.read(&mut chunk).await.unwrap();
                    if count == 0 {
                        return;
                    }
                    bytes.extend_from_slice(&chunk[..count]);
                    if bytes.windows(4).any(|v| v == b"\r\n\r\n") {
                        break;
                    }
                }
                let request = String::from_utf8(bytes).unwrap();
                let path = request.split_whitespace().nth(1).unwrap();
                if status == 0 && path == "/prefix/v1/server-info" {
                    return;
                }
                if path.starts_with("/prefix/v1/scopes/") && hold_scope.load(Ordering::Relaxed) {
                    scope_started.notify_one();
                    scope_release.notified().await;
                }
                if path == "/prefix/v1/scopes/default-exact"
                    && change_access.load(Ordering::Relaxed)
                {
                    access_enabled.store(true, Ordering::Relaxed);
                }
                let (status, body) = match path {
                    "/prefix/v1/server-info" => (status, document),
                    "/prefix/health/ready" => (
                        200,
                        json!({"status":"ready","checks":{"access_mode":if access_enabled.load(Ordering::Relaxed) {"enabled"} else {"disabled"}}}),
                    ),
                    "/prefix/v1/access/me" => (503, json!({"error":{"code":"runtime_not_ready"}})),
                    "/prefix/v1/capabilities" => (403, json!({})),
                    "/prefix/v1/scopes/default" | "/prefix/v1/scopes/default-exact" => (
                        scope_status.load(Ordering::Relaxed),
                        json!({
                            "scope_id":"default-exact", "title":"Personal", "summary":"", "context_references":[],
                            "external_references":[], "version":1
                        }),
                    ),
                    path if path.starts_with("/prefix/v1/scopes?") => {
                        (200, json!({"items":[],"next_cursor":null}))
                    }
                    _ => (200, json!({"status":"ok"})),
                };
                let body = body.to_string();
                let response = format!(
                    "HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = socket.write_all(response.as_bytes()).await;
            });
        }
    });
    let directory = tempfile::tempdir().unwrap();
    let repository = ProfileRepository::open(
        directory.path().join("profiles.json"),
        Arc::new(NativeVault),
    )
    .unwrap();
    let manager = if qualified {
        common::connection_manager(repository)
    } else {
        ConnectionManager::new(repository)
    };
    let state = manager.save_profile(serde_json::from_value(json!({"id":null,"revision":null,"name":"Synthetic",
        "endpoint":endpoint,"authentication":"unauthenticated_loopback","caPem":null,"compatibility":common::PROFILE_ID,
        "keepCredential":false,"credential":null})).unwrap()).unwrap();
    Fixture {
        manager,
        id: state.profiles[0].id.clone(),
        status,
        scope_status,
        hold_scope,
        change_access_on_exact,
        scope_started,
        scope_release,
        server,
        _directory: directory,
    }
}
async fn check(status: u16, document: Value, qualified: bool) -> DiscoveryResult {
    let fixture = fixture(status, document, qualified).await;
    let result = fixture.manager.check(&fixture.id, false).await.unwrap();
    assert!(
        result.active.is_none(),
        "passive discovery must not activate a connection"
    );
    result
        .reports
        .into_iter()
        .next()
        .unwrap()
        .discovery
        .unwrap()
}

#[tokio::test]
async fn qualified_connection_recovers_from_discovery_503_after_explicit_recheck() {
    let fixture = fixture(200, document(), true).await;
    let state = fixture.manager.check(&fixture.id, true).await.unwrap();
    assert!(state.active.unwrap().report.compatibility_verified);

    fixture.status.store(503, Ordering::Relaxed);
    let state = fixture.manager.check(&fixture.id, true).await.unwrap();
    let report = &state.active.as_ref().unwrap().report;
    assert_eq!(
        report.discovery.as_ref().unwrap().status,
        DiscoveryStatus::Unavailable
    );
    assert!(!report.compatibility_verified);
    assert_eq!(
        fixture
            .manager
            .scopes(state.generation, "", None)
            .await
            .err()
            .unwrap()
            .code,
        SafeError::CompatibilityUnverified
    );

    fixture.status.store(200, Ordering::Relaxed);
    let state = fixture.manager.check(&fixture.id, true).await.unwrap();
    let active = state.active.as_ref().unwrap();
    assert_eq!(active.connection_id, fixture.id);
    assert!(active.report.compatibility_verified);
    assert_eq!(
        active.report.discovery.as_ref().unwrap().status,
        DiscoveryStatus::Supported
    );
    assert!(
        fixture
            .manager
            .scopes(state.generation, "", None)
            .await
            .is_ok()
    );
}

#[tokio::test]
async fn compatible_minor_additions_and_base_paths_preserve_independent_qualification() {
    let mut value = document();
    value["schema_version"]["minor"] = json!(50);
    value["schema_version"]["future"] = json!(true);
    value["api_contract_version"]["future"] = json!(true);
    value["new_optional_field"] = json!({"opaque":true});
    value["feature_contracts"]["memory.explicit"]["version"]["minor"] = json!(50);
    value["feature_contracts"]["memory.explicit"]["future"] = json!(true);
    let result = check(200, value, true).await;
    assert_eq!(result.status, DiscoveryStatus::Supported);
    assert_eq!(result.server.unwrap().server_id, "synthetic-server");
    assert_eq!(
        result.operations["remember_memory"].status,
        CompatibilityStatus::Qualified
    );
    assert_eq!(
        result.operations["get_artifact_revision"].status,
        CompatibilityStatus::Qualified
    );
}

#[tokio::test]
async fn handshake_success_does_not_create_qualification() {
    let result = check(200, document(), false).await;
    assert_eq!(result.status, DiscoveryStatus::Supported);
    assert_eq!(
        result.operations["remember_memory"].status,
        CompatibilityStatus::Unverified
    );
    assert_eq!(
        result.operations["remember_memory"].reason,
        CompatibilityReason::QualificationRequired
    );
}

#[tokio::test]
async fn only_a_missing_handshake_allows_explicit_qualified_legacy_operations() {
    let result = check(404, json!({}), true).await;
    assert_eq!(result.status, DiscoveryStatus::Missing);
    assert_eq!(
        result.operations["remember_memory"].status,
        CompatibilityStatus::Qualified
    );
    let result = check(404, json!({}), false).await;
    assert_eq!(
        result.operations["remember_memory"].status,
        CompatibilityStatus::Unverified
    );
    for (code, status) in [
        (0, DiscoveryStatus::Unavailable),
        (401, DiscoveryStatus::Denied),
        (403, DiscoveryStatus::Denied),
        (503, DiscoveryStatus::Unavailable),
    ] {
        let result = check(code, json!({"error":{"code":"runtime_not_ready"}}), true).await;
        assert_eq!(result.status, status);
        assert!(result.error.is_some());
        assert!(result.supported_operations().is_empty());
    }
}

#[tokio::test]
async fn incompatible_products_and_required_major_versions_remain_distinct_from_invalid_documents()
{
    for (field, reason) in [
        ("product", CompatibilityReason::ProductMismatch),
        ("schema_version", CompatibilityReason::SchemaVersion),
        ("api_contract_version", CompatibilityReason::ApiVersion),
    ] {
        let mut value = document();
        if field == "product" {
            value[field] = json!("another-product");
        } else {
            value[field]["major"] = json!(9);
        }
        let result = check(200, value, true).await;
        assert_eq!(result.status, DiscoveryStatus::Incompatible);
        assert_eq!(result.reason, Some(reason));
        assert!(result.supported_operations().is_empty());
    }
    for value in [
        json!({"status":"ok"}),
        {
            let mut value = document();
            value["server_id"] = json!("");
            value
        },
        {
            let mut value = document();
            value["schema_version"]["minor"] = json!(-1);
            value
        },
    ] {
        let result = check(200, value, true).await;
        assert_eq!(result.status, DiscoveryStatus::Invalid);
        assert!(result.supported_operations().is_empty());
    }
}

#[tokio::test]
async fn optional_feature_and_operation_restrictions_do_not_disable_unrelated_scopes() {
    for (change, reason) in [
        ("missing", CompatibilityReason::FeatureMissing),
        ("version", CompatibilityReason::FeatureVersion),
        ("operation", CompatibilityReason::OperationMissing),
    ] {
        let mut value = document();
        match change {
            "missing" => {
                value["feature_contracts"]
                    .as_object_mut()
                    .unwrap()
                    .remove("memory.explicit");
            }
            "version" => {
                value["feature_contracts"]["memory.explicit"]["version"]["major"] = json!(2)
            }
            _ => {
                value["feature_contracts"]["memory.explicit"]["operations"] =
                    json!(["search_memory"])
            }
        }
        let result = check(200, value, true).await;
        assert_eq!(result.status, DiscoveryStatus::Supported);
        assert_eq!(
            result.operations["remember_memory"].status,
            CompatibilityStatus::Unsupported
        );
        assert_eq!(result.operations["remember_memory"].reason, reason);
        assert_eq!(
            result.operations["list_scopes"].status,
            CompatibilityStatus::Qualified
        );
    }
}

#[tokio::test]
async fn explicit_local_handoff_resolves_the_public_default_scope_and_reuses_its_profile() {
    let f = fixture(200, document(), true).await;
    let endpoint = f.manager.state().unwrap().profiles[0].endpoint.clone();
    f.manager.remove_profile(&f.id, 1).unwrap();
    let before = f.manager.state().unwrap();
    let selected = f
        .manager
        .connect_local(
            before.generation,
            endpoint.clone(),
            common::PROFILE_ID.into(),
            "display-only-version".into(),
        )
        .await
        .unwrap();
    assert_eq!(
        selected
            .active
            .as_ref()
            .unwrap()
            .scope
            .as_ref()
            .unwrap()
            .scope_id,
        "default-exact"
    );
    assert_eq!(selected.profiles.len(), 1);
    let id = selected.active.as_ref().unwrap().connection_id.clone();
    let again = f
        .manager
        .connect_local(
            selected.generation,
            endpoint.clone(),
            common::PROFILE_ID.into(),
            "display-only-version".into(),
        )
        .await
        .unwrap();
    assert_eq!(again.profiles.len(), 1);
    assert_eq!(again.active.as_ref().unwrap().connection_id, id);
    assert_eq!(
        f.manager
            .connect_local(
                before.generation,
                endpoint,
                common::PROFILE_ID.into(),
                "display-only-version".into()
            )
            .await
            .err()
            .unwrap()
            .code,
        SafeError::StaleContext
    );
    assert_eq!(f.manager.state().unwrap().generation, again.generation);
}

#[tokio::test]
async fn local_handoff_cannot_select_a_scope_without_qualified_discovery() {
    let f = fixture(503, document(), true).await;
    let before = f.manager.state().unwrap();
    assert_eq!(
        f.manager
            .connect_local(
                before.generation,
                before.profiles[0].endpoint.clone(),
                common::PROFILE_ID.into(),
                "display-only-version".into()
            )
            .await
            .err()
            .unwrap()
            .code,
        SafeError::CompatibilityUnverified
    );
    assert!(f.manager.state().unwrap().active.is_none());
}

#[tokio::test]
async fn a_different_server_version_cannot_leave_an_activated_local_context() {
    let f = fixture(200, document(), true).await;
    let before = f.manager.state().unwrap();
    assert_eq!(
        f.manager
            .connect_local(
                before.generation,
                before.profiles[0].endpoint.clone(),
                common::PROFILE_ID.into(),
                "different-resource-version".into()
            )
            .await
            .err()
            .unwrap()
            .code,
        SafeError::CompatibilityUnverified
    );
    assert!(f.manager.state().unwrap().active.is_none());
}

#[tokio::test]
async fn local_handoff_requires_the_complete_basic_memory_operation_set() {
    let mut info = document();
    info["feature_contracts"]["memory.explicit"]["operations"] = json!(["search_memory"]);
    let f = fixture(200, info, true).await;
    let before = f.manager.state().unwrap();
    assert_eq!(
        f.manager
            .connect_local(
                before.generation,
                before.profiles[0].endpoint.clone(),
                common::PROFILE_ID.into(),
                "display-only-version".into()
            )
            .await
            .err()
            .unwrap()
            .code,
        SafeError::CompatibilityUnverified
    );
    assert!(f.manager.state().unwrap().active.is_none());
}

#[tokio::test]
async fn a_pending_or_failed_default_scope_never_publishes_a_local_activation() {
    let f = fixture(200, document(), true).await;
    f.hold_scope.store(true, Ordering::Relaxed);
    let before = f.manager.state().unwrap();
    let (result, ()) = tokio::time::timeout(std::time::Duration::from_secs(5), async {
        tokio::join!(
            f.manager.connect_local(
                before.generation,
                before.profiles[0].endpoint.clone(),
                common::PROFILE_ID.into(),
                "display-only-version".into()
            ),
            async {
                f.scope_started.notified().await;
                assert!(f.manager.state().unwrap().active.is_none());
                f.scope_status.store(403, Ordering::Relaxed);
                f.hold_scope.store(false, Ordering::Relaxed);
                f.scope_release.notify_one();
            }
        )
    })
    .await
    .unwrap();
    assert_eq!(result.err().unwrap().code, SafeError::Forbidden);
    let state = f.manager.state().unwrap();
    assert!(state.active.is_none());
    assert!(state.reports.is_empty());
}

#[tokio::test]
async fn ordinary_refresh_reauthorizes_the_local_default_scope_and_drops_revoked_selection() {
    let f = fixture(200, document(), true).await;
    let before = f.manager.state().unwrap();
    let selected = f
        .manager
        .connect_local(
            before.generation,
            before.profiles[0].endpoint.clone(),
            common::PROFILE_ID.into(),
            "display-only-version".into(),
        )
        .await
        .unwrap();
    let id = selected.active.as_ref().unwrap().connection_id.clone();
    let refreshed = f.manager.check(&id, false).await.unwrap();
    assert_eq!(
        refreshed.active.unwrap().scope.unwrap().scope_id,
        "default-exact"
    );
    f.scope_status.store(403, Ordering::Relaxed);
    let revoked = f.manager.check(&id, false).await.unwrap();
    assert!(revoked.active.unwrap().scope.is_none());
}

#[tokio::test]
async fn published_local_scope_is_newer_than_pending_activation_snapshots() {
    let f = fixture(200, document(), true).await;
    f.hold_scope.store(true, Ordering::Relaxed);
    let before = f.manager.state().unwrap();
    let (result, pending_generation) =
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            tokio::join!(
                f.manager.connect_local(
                    before.generation,
                    before.profiles[0].endpoint.clone(),
                    common::PROFILE_ID.into(),
                    "display-only-version".into()
                ),
                async {
                    f.scope_started.notified().await;
                    let pending = f.manager.state().unwrap();
                    assert!(pending.active.is_none());
                    f.hold_scope.store(false, Ordering::Relaxed);
                    f.scope_release.notify_one();
                    pending.generation
                }
            )
        })
        .await
        .unwrap();
    let published = result.unwrap();
    assert!(published.generation > pending_generation);
    assert_eq!(
        published.active.unwrap().scope.unwrap().scope_id,
        "default-exact"
    );
}

#[tokio::test]
async fn local_handoff_rejects_identity_changes_during_exact_scope_resolution() {
    let f = fixture(200, document(), true).await;
    f.change_access_on_exact.store(true, Ordering::Relaxed);
    let before = f.manager.state().unwrap();
    let result = f
        .manager
        .connect_local(
            before.generation,
            before.profiles[0].endpoint.clone(),
            common::PROFILE_ID.into(),
            "display-only-version".into(),
        )
        .await;
    assert_eq!(result.err().unwrap().code, SafeError::RuntimeNotReady);
    assert!(f.manager.state().unwrap().active.is_none());
}
