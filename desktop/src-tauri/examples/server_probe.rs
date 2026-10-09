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

//! Only invoked by the isolated real-Server harness; never registered as an IPC command.
use powercontext_desktop::{
    connections::{
        profiles::ProfileRepository,
        session::{ConnectionManager, WriteStatus},
    },
    credentials::{Secret, WindowsVault},
    error::SafeError,
    transport::{Endpoint, ServerApi},
};
use serde::Deserialize;
#[derive(Deserialize)]
struct Fixture {
    response_loss_path: Option<String>,
    identity_change_path: Option<String>,
    endpoint: String,
    scope_id: String,
    token: Option<Secret>,
    ca_pem: Option<String>,
}
#[tokio::main]
async fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).is_some_and(|a| a == "certificates") {
        use rcgen::{
            BasicConstraints, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair,
            KeyUsagePurpose,
        };
        let dir = std::path::Path::new(&args[2]);
        let mut ca = CertificateParams::new(vec![]).unwrap();
        ca.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        ca.distinguished_name
            .push(rcgen::DnType::CommonName, "Desktop test CA");
        ca.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let ca_key = KeyPair::generate().unwrap();
        let ca_cert = ca.self_signed(&ca_key).unwrap();
        let issuer = Issuer::new(ca, ca_key);
        let mut leaf =
            CertificateParams::new(vec!["localhost".into(), "127.0.0.1".into()]).unwrap();
        leaf.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
        let key = KeyPair::generate().unwrap();
        let cert = leaf.signed_by(&key, &issuer).unwrap();
        std::fs::write(dir.join("ca.pem"), ca_cert.pem()).unwrap();
        std::fs::write(dir.join("server.pem"), cert.pem()).unwrap();
        std::fs::write(dir.join("server-key.pem"), key.serialize_pem()).unwrap();
        return;
    }
    let mut fixture: Fixture = serde_json::from_slice(&std::fs::read(&args[1]).unwrap()).unwrap();
    let has_token = fixture.token.is_some();
    let endpoint = Endpoint::parse(&fixture.endpoint).unwrap();
    let api = ServerApi::new(
        endpoint.clone(),
        fixture.ca_pem.as_deref().map(str::as_bytes),
        fixture.token.take(),
    )
    .unwrap();
    api.live().await.unwrap();
    api.readiness().await.unwrap();
    if has_token {
        api.principal().await.unwrap();
    } else {
        assert_eq!(
            api.principal().await.err().unwrap().code,
            SafeError::RuntimeNotReady
        );
        assert_eq!(
            api.readiness()
                .await
                .unwrap()
                .checks
                .get("access_mode")
                .map(String::as_str),
            Some("disabled")
        );
    }
    let capabilities = api.capabilities().await.unwrap();
    assert!(capabilities.artifact_families.iter().any(|v| v == "memory"));
    api.default_scope().await.unwrap();
    let scopes = api.scopes("", None).await.unwrap();
    assert!(!scopes.items.is_empty());
    assert_eq!(
        api.scope(&fixture.scope_id).await.unwrap().scope_id,
        fixture.scope_id
    );
    let keyword = format!("desktop{}", uuid::Uuid::new_v4().simple());
    let text = format!("{keyword} 中文记录 café\nSecond line.");
    let saved = api.remember(&fixture.scope_id, &text).await.unwrap();
    let entry = saved
        .entry
        .expect("unique synthetic note must produce an exact entry");
    let results = api.search(&fixture.scope_id, &keyword).await.unwrap();
    assert!(
        results
            .hits
            .iter()
            .any(|hit| hit.citation == entry.citation)
    );
    let exact = api.entry(&fixture.scope_id, &entry.citation).await.unwrap();
    assert_eq!(exact.text, text);
    // Exercise the same native context owner used by product IPC, not only bare HTTP adapters.
    let temporary = tempfile::tempdir().unwrap();
    let manager = ConnectionManager::new(
        ProfileRepository::open(
            temporary.path().join("profiles.json"),
            std::sync::Arc::new(WindowsVault),
        )
        .unwrap(),
    );
    let raw: serde_json::Value = serde_json::from_slice(&std::fs::read(&args[1]).unwrap()).unwrap();
    let compatibility = manager.state().unwrap().compatibility_profiles[0]
        .id
        .clone();
    let credential = raw["token"]
        .as_str()
        .map(|value| serde_json::json!({"secret":value,"storage":"session_only"}));
    let profile = manager.save_profile(serde_json::from_value(serde_json::json!({
        "id":null,"revision":null,"name":"Synthetic integration", "endpoint":fixture.endpoint,
        "authentication":if has_token { "bearer" } else { "unauthenticated_loopback" },
        "caPem":fixture.ca_pem,"compatibility":compatibility,"keepCredential":false,"credential":credential
    })).unwrap()).unwrap().profiles[0].id.clone();
    let generation = manager.check(&profile, true).await.unwrap().generation;
    let generation = manager
        .select_scope(generation, &fixture.scope_id)
        .await
        .unwrap()
        .generation;
    let keyword = format!("context{}", uuid::Uuid::new_v4().simple());
    let submitted = format!("  {keyword} 中文 café\nSecond line.  ");
    let expected = format!("{keyword} 中文 café\nSecond line.");
    let saved = manager.remember(generation, &submitted).await.unwrap();
    assert_eq!(saved.record.status, WriteStatus::Succeeded);
    let entry = saved.result.unwrap().entry.unwrap();
    assert_eq!(entry.text, expected);
    let matches = manager.search_memory(generation, &keyword).await.unwrap();
    assert!(
        matches
            .hits
            .iter()
            .any(|hit| hit.citation == entry.citation)
    );
    let exact = manager
        .memory_entry(generation, &entry.citation)
        .await
        .unwrap();
    assert_eq!(exact.text, expected);
    assert_eq!(matches.hits.len(), 1);
    assert!(
        manager
            .search_memory(generation, "absentuniquefixtureword")
            .await
            .unwrap()
            .hits
            .is_empty()
    );
    let batch = format!("batch{}", uuid::Uuid::new_v4().simple());
    let mut save_ms = vec![];
    for index in 0..11 {
        let start = std::time::Instant::now();
        let result = manager
            .remember(generation, &format!("{batch} synthetic note {index}"))
            .await
            .unwrap();
        save_ms.push(start.elapsed().as_secs_f64() * 1000.0);
        assert_eq!(result.record.status, WriteStatus::Succeeded);
    }
    let mut search_ms = vec![];
    let mut exact_ms = vec![];
    for _ in 0..20 {
        let start = std::time::Instant::now();
        let matches = manager.search_memory(generation, &batch).await.unwrap();
        search_ms.push(start.elapsed().as_secs_f64() * 1000.0);
        assert_eq!(matches.hits.len(), 10);
        let start = std::time::Instant::now();
        let exact = manager
            .memory_entry(generation, &entry.citation)
            .await
            .unwrap();
        exact_ms.push(start.elapsed().as_secs_f64() * 1000.0);
        assert_eq!(exact.text, expected);
    }
    if let Some(path) = &fixture.response_loss_path {
        std::fs::write(path, b"0").unwrap();
        let keyword = format!("lostresponse{}", uuid::Uuid::new_v4().simple());
        let text = format!("{keyword} committed synthetic note");
        let outcome = manager.remember(generation, &text).await.unwrap();
        assert_eq!(outcome.record.status, WriteStatus::Unknown);
        assert!(outcome.result.is_none());
        let results = manager.search_memory(generation, &keyword).await.unwrap();
        assert_eq!(results.hits.len(), 1);
        let committed = manager
            .memory_entry(generation, &results.hits[0].citation)
            .await
            .unwrap();
        assert_eq!(committed.text, text);
        assert_eq!(std::fs::read_to_string(path).unwrap(), "1");
    }
    if let Some(reader_token) = raw["reader_token"].as_str() {
        verify_revocation(&fixture, &raw, reader_token, &entry.citation, &expected).await;
        verify_scope_pages(&fixture, &raw, &manager, generation).await;
    }
    if let Some(path) = fixture.identity_change_path {
        // Test-only out-of-band provider control, not a product endpoint or IPC command.
        std::fs::write(path, b"change").unwrap();
        assert_eq!(
            manager
                .memory_entry(generation, &entry.citation)
                .await
                .err()
                .unwrap()
                .code,
            SafeError::StaleContext
        );
        let state = manager.state().unwrap();
        assert!(state.active.is_none());
        assert!(state.generation > generation);
        assert_eq!(
            api.entry(&fixture.scope_id, &entry.citation)
                .await
                .err()
                .unwrap()
                .code,
            SafeError::Forbidden
        );
    }
    manager.disconnect().unwrap();
    api.live().await.unwrap();
    if has_token {
        let bad = ServerApi::new(
            endpoint,
            fixture.ca_pem.as_deref().map(str::as_bytes),
            Some(Secret::new("wrong-synthetic-token".into()).unwrap()),
        )
        .unwrap();
        assert_eq!(
            bad.principal().await.err().unwrap().code,
            SafeError::Unauthorized
        );
    }
    println!(
        "{}",
        serde_json::json!({
            "result":"passed",
            "performance": {
                "scope":"Native ConnectionManager round trips, including identity recheck; local fixture; no UI latency or approved budget",
                "noteCount":13,
                "save": distribution(save_ms), "search": distribution(search_ms), "exactRead": distribution(exact_ms)
            }
        })
    );
}

fn distribution(mut values: Vec<f64>) -> serde_json::Value {
    values.sort_by(f64::total_cmp);
    let percentile = |p: f64| values[(values.len() as f64 * p).ceil() as usize - 1];
    serde_json::json!({"samplesMs":values, "count":values.len(), "p50Ms":percentile(0.5), "p95Ms":percentile(0.95)})
}

async fn verify_revocation(
    fixture: &Fixture,
    raw: &serde_json::Value,
    reader_token: &str,
    citation: &powercontext_desktop::transport::wire::MemoryCitation,
    expected: &str,
) {
    // Only the isolated test administrator mutates fixture policy; never exposed to Desktop IPC.
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let admin = raw["token"].as_str().unwrap();
    let binding: serde_json::Value = client
        .post(format!("{}/v1/access/bindings/create", fixture.endpoint))
        .bearer_auth(admin)
        .json(&serde_json::json!({
            "subject":{"type":"user","id":"desktop-fixture-reader"},
            "resource":{"type":"scope","scope_id":fixture.scope_id},
            "role":"scope.viewer","idempotency_key":"desktop-reader-grant"
        }))
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap()
        .json()
        .await
        .unwrap();
    let reader = ServerApi::new(
        Endpoint::parse(&fixture.endpoint).unwrap(),
        None,
        Some(Secret::new(reader_token.into()).unwrap()),
    )
    .unwrap();
    let principal = reader.principal().await.unwrap();
    assert_eq!(
        reader
            .entry(&fixture.scope_id, citation)
            .await
            .unwrap()
            .text,
        expected
    );
    client
        .post(format!("{}/v1/access/bindings/revoke", fixture.endpoint))
        .bearer_auth(admin)
        .json(&serde_json::json!({
            "binding_id":binding["binding_id"],"expected_version":binding["version"],
            "idempotency_key":"desktop-reader-revoke"
        }))
        .send()
        .await
        .unwrap()
        .error_for_status()
        .unwrap();
    assert_eq!(reader.principal().await.unwrap(), principal);
    assert_eq!(
        reader
            .entry(&fixture.scope_id, citation)
            .await
            .err()
            .unwrap()
            .code,
        SafeError::Forbidden
    );
}

async fn verify_scope_pages(
    fixture: &Fixture,
    raw: &serde_json::Value,
    manager: &ConnectionManager,
    generation: u32,
) {
    let client = reqwest::Client::builder().no_proxy().build().unwrap();
    let title = format!("paging{}", uuid::Uuid::new_v4().simple());
    let mut created = std::collections::BTreeSet::new();
    for index in 0..51 {
        let scope: serde_json::Value = client
            .post(format!("{}/v1/scopes", fixture.endpoint))
            .bearer_auth(raw["token"].as_str().unwrap())
            .json(&serde_json::json!({
                "title":title,"summary":"Synthetic same-title pagination fixture",
                "idempotency_key":format!("{title}-{index}")
            }))
            .send()
            .await
            .unwrap()
            .error_for_status()
            .unwrap()
            .json()
            .await
            .unwrap();
        assert!(created.insert(scope["scope_id"].as_str().unwrap().to_owned()));
    }
    let first = manager.scopes(generation, &title, None).await.unwrap();
    assert_eq!(first.items.len(), 50);
    let second = manager
        .scopes(generation, &title, first.next_cursor.as_deref())
        .await
        .unwrap();
    assert!(first.next_cursor.is_some());
    assert_eq!(second.items.len(), 1);
    assert!(second.next_cursor.is_none());
    let found: std::collections::BTreeSet<_> = first
        .items
        .iter()
        .chain(second.items.iter())
        .map(|scope| scope.scope_id.clone())
        .collect();
    assert_eq!(found, created);
    // Exact lookups distinguish the same display name without modifying the active memory Scope.
    let api = ServerApi::new(
        Endpoint::parse(&fixture.endpoint).unwrap(),
        None,
        Some(Secret::new(raw["token"].as_str().unwrap().into()).unwrap()),
    )
    .unwrap();
    for id in [created.first().unwrap(), created.last().unwrap()] {
        assert_eq!(&api.scope(id).await.unwrap().scope_id, id);
    }
    assert_eq!(
        manager
            .state()
            .unwrap()
            .active
            .unwrap()
            .scope
            .unwrap()
            .scope_id,
        fixture.scope_id
    );
}
