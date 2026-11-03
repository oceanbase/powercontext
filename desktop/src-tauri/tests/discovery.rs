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
    atomic::{AtomicU16, Ordering},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
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
    let server = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            let document = document.clone();
            let status = response_status.load(Ordering::Relaxed);
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
                let (status, body) = match path {
                    "/prefix/v1/server-info" => (status, document),
                    "/prefix/health/ready" => (
                        200,
                        json!({"status":"ready","checks":{"access_mode":"disabled"}}),
                    ),
                    "/prefix/v1/access/me" => (503, json!({"error":{"code":"runtime_not_ready"}})),
                    "/prefix/v1/capabilities" => (403, json!({})),
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
