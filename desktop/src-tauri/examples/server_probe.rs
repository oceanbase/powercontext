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
        session::{CompatibilityProfile, ConnectionManager, WriteStatus},
    },
    credentials::{Secret, WindowsVault},
    error::SafeError,
    transport::{
        Endpoint, MemoryEntryResult, MemoryMutationResult, MemoryReference, MemorySearchResponse,
        ServerApi,
        wire::{ArtifactReference, MemoryMutationResponse, SearchMemoryResponse},
    },
};
use serde::Deserialize;
#[derive(Deserialize)]
struct Fixture {
    compatibility_profile: CompatibilityProfile,
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
    let mut saved = atomic_write(api.remember(&fixture.scope_id, &text).await.unwrap());
    assert_eq!(
        saved.records.len(),
        1,
        "unique synthetic note must produce one Atomic record"
    );
    let entry = saved.records.pop().unwrap();
    let reference = MemoryReference::Artifact {
        artifact: entry.artifact.clone(),
    };
    let results = atomic_search(api.search(&fixture.scope_id, &keyword).await.unwrap());
    assert!(
        results
            .hits
            .iter()
            .any(|hit| hit.memory.artifact == entry.artifact)
    );
    let exact = api.entry(&fixture.scope_id, &reference).await.unwrap();
    assert_exact_atomic(&exact, &fixture.scope_id, &entry.artifact, &text);
    // Exercise the same native context owner used by product IPC, not only bare HTTP adapters.
    let temporary = tempfile::tempdir().unwrap();
    let manager = ConnectionManager::with_compatibility(
        ProfileRepository::open(
            temporary.path().join("profiles.json"),
            std::sync::Arc::new(WindowsVault),
        )
        .unwrap(),
        vec![fixture.compatibility_profile.clone()],
    );
    let raw: serde_json::Value = serde_json::from_slice(&std::fs::read(&args[1]).unwrap()).unwrap();
    let compatibility = &fixture.compatibility_profile.id;
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
    assert_eq!(
        saved.record.references,
        saved.result.as_ref().unwrap().references()
    );
    let mut saved = atomic_write(saved.result.unwrap());
    assert_eq!(
        saved.records.len(),
        1,
        "unique synthetic note must produce one Atomic record"
    );
    let entry = saved.records.pop().unwrap();
    let reference = MemoryReference::Artifact {
        artifact: entry.artifact.clone(),
    };
    assert_eq!(entry.text, expected);
    let matches = atomic_search(manager.search_memory(generation, &keyword).await.unwrap());
    assert!(
        matches
            .hits
            .iter()
            .any(|hit| hit.memory.artifact == entry.artifact)
    );
    let exact = manager.memory_entry(generation, &reference).await.unwrap();
    assert_exact_atomic(&exact, &fixture.scope_id, &entry.artifact, &expected);
    assert_eq!(matches.hits.len(), 1);
    assert!(
        atomic_search(
            manager
                .search_memory(generation, "absentuniquefixtureword")
                .await
                .unwrap()
        )
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
        let expected_references = result.result.as_ref().unwrap().references();
        assert_eq!(result.record.references, expected_references);
        let records = atomic_write(result.result.unwrap()).records;
        assert_eq!(result.record.references.len(), records.len());
        assert!(!records.is_empty());
        for (record, reference) in records.iter().zip(&result.record.references) {
            let exact = manager.memory_entry(generation, reference).await.unwrap();
            assert_exact_atomic(&exact, &fixture.scope_id, &record.artifact, &record.text);
        }
    }
    let mut search_ms = vec![];
    let mut exact_ms = vec![];
    for _ in 0..20 {
        let start = std::time::Instant::now();
        let matches = atomic_search(manager.search_memory(generation, &batch).await.unwrap());
        search_ms.push(start.elapsed().as_secs_f64() * 1000.0);
        assert_eq!(matches.hits.len(), 10);
        let start = std::time::Instant::now();
        let exact = manager.memory_entry(generation, &reference).await.unwrap();
        exact_ms.push(start.elapsed().as_secs_f64() * 1000.0);
        assert_exact_atomic(&exact, &fixture.scope_id, &entry.artifact, &expected);
    }
    if let Some(path) = &fixture.response_loss_path {
        std::fs::write(path, b"0").unwrap();
        let keyword = format!("lostresponse{}", uuid::Uuid::new_v4().simple());
        let text = format!("{keyword} committed synthetic note");
        let outcome = manager.remember(generation, &text).await.unwrap();
        assert_eq!(outcome.record.status, WriteStatus::Unknown);
        assert!(outcome.result.is_none());
        let mut results = atomic_search(manager.search_memory(generation, &keyword).await.unwrap());
        assert_eq!(results.hits.len(), 1);
        let committed_record = results.hits.pop().unwrap().memory;
        let committed_reference = MemoryReference::Artifact {
            artifact: committed_record.artifact.clone(),
        };
        let committed = manager
            .memory_entry(generation, &committed_reference)
            .await
            .unwrap();
        assert_exact_atomic(
            &committed,
            &fixture.scope_id,
            &committed_record.artifact,
            &text,
        );
        assert_eq!(std::fs::read_to_string(path).unwrap(), "1");
    }
    if let Some(reader_token) = raw["reader_token"].as_str() {
        verify_revocation(&fixture, &raw, reader_token, &reference, &expected).await;
        verify_scope_pages(&fixture, &raw, &manager, generation).await;
    }
    if let Some(path) = fixture.identity_change_path {
        // Test-only out-of-band provider control, not a product endpoint or IPC command.
        std::fs::write(path, b"change").unwrap();
        assert_eq!(
            manager
                .memory_entry(generation, &reference)
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
            api.entry(&fixture.scope_id, &reference)
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

fn atomic_write(result: MemoryMutationResult) -> MemoryMutationResponse {
    let MemoryMutationResult::Atomic(result) = result else {
        panic!("current real Server must return the Atomic mutation protocol");
    };
    result
}

fn atomic_search(result: MemorySearchResponse) -> SearchMemoryResponse {
    let MemorySearchResponse::Atomic(result) = result else {
        panic!("current real Server must return the Atomic search protocol");
    };
    result
}

fn assert_exact_atomic(
    result: &MemoryEntryResult,
    scope: &str,
    reference: &ArtifactReference,
    text: &str,
) {
    let MemoryEntryResult::Atomic(revision) = result else {
        panic!("Atomic references must read immutable Atomic revisions");
    };
    assert_eq!(revision.scope_id, scope);
    assert_eq!(revision.family, reference.family);
    assert_eq!(revision.artifact_id, reference.artifact_id);
    assert_eq!(revision.revision, reference.revision);
    assert_eq!(revision.content.schema, "powercontext.atomic-memory.v1");
    assert_eq!(revision.content.text, text);
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
    reference: &MemoryReference,
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
    let exact = reader.entry(&fixture.scope_id, reference).await.unwrap();
    let MemoryReference::Artifact { artifact } = reference else {
        panic!("revocation fixture must use its saved exact Atomic reference");
    };
    assert_exact_atomic(&exact, &fixture.scope_id, artifact, expected);
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
            .entry(&fixture.scope_id, reference)
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
