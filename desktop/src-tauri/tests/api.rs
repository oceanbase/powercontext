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
    error::SafeError,
    session_observations::{
        ObservationOutcome, ObservationUnit, ObservedOperation, SessionObservations,
    },
    transport::{
        Endpoint, ServerApi,
        wire::{ArtifactReference, ReadinessStatus},
    },
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};

async fn fixture(
    status: u16,
    body: impl Into<String>,
) -> (ServerApi, tokio::task::JoinHandle<String>) {
    let body = body.into();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Endpoint::parse(&format!(
        "http://{}/proxy/%E4%B8%AD%E6%96%87",
        listener.local_addr().unwrap()
    ))
    .unwrap();
    let task = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut bytes = vec![];
        loop {
            let mut buf = [0; 1024];
            let n = socket.read(&mut buf).await.unwrap();
            if n == 0 {
                break;
            }
            bytes.extend_from_slice(&buf[..n]);
            if let Some(end) = bytes.windows(4).position(|v| v == b"\r\n\r\n") {
                let headers = String::from_utf8_lossy(&bytes[..end]);
                let size: usize = headers
                    .lines()
                    .find_map(|l| {
                        l.to_lowercase()
                            .strip_prefix("content-length: ")
                            .map(str::to_owned)
                    })
                    .map(|s| s.parse().unwrap())
                    .unwrap_or(0);
                if bytes.len() >= end + 4 + size {
                    break;
                }
            }
        }
        socket.write_all(format!("HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nX-PowerContext-Request-ID: safe-request-1\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).await.unwrap();
        String::from_utf8(bytes).unwrap()
    });
    (ServerApi::new(endpoint, None, None).unwrap(), task)
}
#[tokio::test]
async fn scope_paging_is_bounded_and_preserves_opaque_cursor_and_prefix() {
    let (api, request) = fixture(200, r#"{"items":[],"next_cursor":null}"#).await;
    api.scopes("中文 & title", Some("opaque+/=?"))
        .await
        .unwrap();
    let request = request.await.unwrap();
    assert!(request.starts_with("GET /proxy/%E4%B8%AD%E6%96%87/v1/scopes?limit=50&"));
    assert!(request.contains("query_field=title"));
    assert!(request.contains("cursor=opaque%2B%2F%3D%3F"));
}
#[tokio::test]
async fn exact_scope_uses_one_encoded_path_segment() {
    let (api, request) = fixture(404, "private body").await;
    assert_eq!(
        api.scope("scope/中文?x").await.err().unwrap().code,
        SafeError::NotFound
    );
    assert!(
        request
            .await
            .unwrap()
            .starts_with("GET /proxy/%E4%B8%AD%E6%96%87/v1/scopes/scope%2F%E4%B8%AD%E6%96%87%3Fx ")
    );
}

#[tokio::test]
async fn discovery_requests_are_observed_without_counting_a_user_operation() {
    for status in [200, 404] {
        let observations = SessionObservations::default();
        let body = if status == 200 {
            r#"{"schema_version":{"major":1,"minor":0},"product":"powercontext","server_id":"private-server","package_version":"1.4.0","api_contract_version":{"major":1,"minor":4},"feature_contracts":{}}"#
        } else {
            "private discovery error"
        };
        let (api, request) = fixture(status, body).await;
        let result = api
            .with_observations(observations.clone())
            .server_info()
            .await;
        if status == 200 {
            assert!(result.is_ok());
        } else {
            assert_eq!(result.err().unwrap().code, SafeError::NotFound);
        }
        assert!(
            request
                .await
                .unwrap()
                .starts_with("GET /proxy/%E4%B8%AD%E6%96%87/v1/server-info ")
        );
        let summary = observations.summary().unwrap();
        assert!(
            summary
                .coverage
                .http_operations
                .contains(&ObservedOperation::GetServerInfo)
        );
        assert!(
            !summary
                .coverage
                .user_operations
                .contains(&ObservedOperation::GetServerInfo)
        );
        assert_eq!(summary.recent.len(), 1);
        let record = &summary.recent[0];
        assert_eq!(record.unit, ObservationUnit::HttpRequest);
        assert_eq!(record.operation, ObservedOperation::GetServerInfo);
        assert_eq!(record.http_status, Some(status));
        assert_eq!(
            record.outcome,
            if status == 200 {
                ObservationOutcome::Success
            } else {
                ObservationOutcome::Failure
            }
        );
        assert!(!observations.diagnostic_text().unwrap().contains("private"));
    }
}

#[tokio::test]
async fn discovery_rejects_duplicate_deployment_identity_fields() {
    let (api, request) = fixture(200, r#"{"schema_version":{"major":1,"minor":0},"product":"powercontext","server_id":"old","server_id":"new","package_version":"1.4.0","api_contract_version":{"major":1,"minor":4},"feature_contracts":{}}"#).await;
    assert_eq!(
        api.server_info().await.err().unwrap().code,
        SafeError::InvalidResponse
    );
    assert!(
        request
            .await
            .unwrap()
            .starts_with("GET /proxy/%E4%B8%AD%E6%96%87/v1/server-info ")
    );
}
#[tokio::test]
async fn readiness_failure_is_a_fact_and_write_failure_keeps_dispatch_uncertainty() {
    let (api, request) = fixture(
        503,
        r#"{"status":"not_ready","checks":{"database":"not_ready"}}"#,
    )
    .await;
    assert_eq!(
        api.readiness().await.unwrap().status,
        ReadinessStatus::NotReady
    );
    request.await.unwrap();
    let (api, request) = fixture(500, "private body synthetic-secret").await;
    let error = api
        .remember("selected-scope", "synthetic note")
        .await
        .err()
        .unwrap();
    assert!(error.dispatched);
    assert_eq!(error.code, SafeError::Server);
    assert_eq!(error.request_id.as_deref(), Some("safe-request-1"));
    assert!(
        !serde_json::to_string(&error)
            .unwrap()
            .contains("private body")
    );
    let request = request.await.unwrap();
    let body: serde_json::Value =
        serde_json::from_str(request.split("\r\n\r\n").nth(1).unwrap()).unwrap();
    assert_eq!(
        body,
        serde_json::json!({"scope_id":"selected-scope","kind":"note","text":"synthetic note"})
    );
}

#[tokio::test]
async fn request_observations_keep_http_status_and_exclude_private_response_and_request_data() {
    let observations = SessionObservations::default();
    let (api, task) = fixture(
        503,
        r#"{"status":"not_ready","checks":{"private-setting":"private-value"}}"#,
    )
    .await;
    let api = api.with_observations(observations.clone());
    assert_eq!(
        api.readiness().await.unwrap().status,
        ReadinessStatus::NotReady
    );
    task.await.unwrap();
    let (api, task) = fixture(200, r#"{"status":"ok"}"#).await;
    api.with_observations(observations.clone())
        .live()
        .await
        .unwrap();
    task.await.unwrap();
    let (api, task) = fixture(500, "private response and exception").await;
    let error = api
        .with_observations(observations.clone())
        .remember("private-scope", "private note")
        .await
        .err()
        .unwrap();
    assert!(error.dispatched);
    task.await.unwrap();
    let summary = observations.summary().unwrap();
    assert_eq!(summary.recent.len(), 3);
    assert!(
        summary
            .recent
            .iter()
            .all(|record| record.unit == ObservationUnit::HttpRequest)
    );
    assert_eq!(summary.recent[0].operation, ObservedOperation::GetReadiness);
    assert_eq!(summary.recent[0].http_status, Some(503));
    assert_eq!(summary.recent[0].outcome, ObservationOutcome::Failure);
    assert_eq!(
        summary.recent[0].error_code,
        Some(SafeError::RuntimeNotReady)
    );
    assert_eq!(summary.recent[1].outcome, ObservationOutcome::Success);
    assert_eq!(summary.recent[2].http_status, Some(500));
    assert_eq!(summary.recent[2].outcome, ObservationOutcome::Failure);
    let text = observations.diagnostic_text().unwrap();
    for private in [
        "private",
        "safe-request-1",
        "127.0.0.1",
        "proxy",
        "中文",
        "requestId",
        "scopeId",
        "endpoint",
    ] {
        assert!(!text.contains(private), "{private}");
    }
    // Native preview is the same safe schema as the structured IPC result.
    // Round-trip the expected JSON too so float parsing has the same rounding behavior.
    let projected: serde_json::Value = serde_json::from_str(&text).unwrap();
    let expected: serde_json::Value =
        serde_json::from_str(&serde_json::to_string_pretty(&summary.recent).unwrap()).unwrap();
    assert_eq!(projected["recent"], expected);
}
#[tokio::test]
async fn errors_are_distinct_without_anonymous_fallback() {
    for (status, code) in [
        (401, SafeError::Unauthorized),
        (403, SafeError::Forbidden),
        (410, SafeError::CursorExpired),
    ] {
        let (api, task) = fixture(status, "sensitive failure details").await;
        assert_eq!(api.scopes("", None).await.err().unwrap().code, code);
        task.await.unwrap();
    }
    let (api, task) = fixture(
        503,
        r#"{"error":{"code":"authentication_unavailable","message":"sensitive failure details"}}"#,
    )
    .await;
    assert_eq!(
        api.principal().await.err().unwrap().code,
        SafeError::AuthenticationUnavailable
    );
    task.await.unwrap();
}
#[test]
fn encoded_base_paths_reject_escape_aliases_and_support_unicode() {
    for endpoint in [
        "http://127.1/",
        "http://2130706433/",
        "http://localhost/a/%2e%2e/b",
        "http://localhost/%252e",
        "http://localhost/a%2fb",
        "http://localhost/%xx",
        "http://localhost/%ff",
    ] {
        assert!(Endpoint::parse(endpoint).is_err(), "{endpoint}");
    }
    assert_eq!(
        Endpoint::parse("http://localhost/中文/").unwrap().as_str(),
        "http://localhost/%E4%B8%AD%E6%96%87/"
    );
    assert_eq!(
        Endpoint::parse("http://localhost/%E4%B8%AD%E6%96%87/")
            .unwrap()
            .as_str(),
        "http://localhost/%E4%B8%AD%E6%96%87/"
    );
}

#[tokio::test]
async fn invalid_successful_write_response_keeps_dispatch_uncertainty() {
    let (api, request) = fixture(200, r#"{"changed":true,"records":[{"artifact":{"family":"atomic-memory","artifact_id":"a","revision":9007199254740992},"kind":"note","text":"synthetic note","state":"active","state_version":0,"merged_into_id":null}]}"#).await;
    let error = api
        .remember("scope-a", "synthetic note")
        .await
        .err()
        .unwrap();
    assert_eq!(error.code, SafeError::InvalidResponse);
    assert!(error.dispatched);
    request.await.unwrap();
}

#[tokio::test]
async fn atomic_records_survive_save_search_and_exact_revision_read() {
    let artifact =
        serde_json::json!({"family":"atomic-memory","artifact_id":"atomic/a","revision":7});
    let kind = "中".repeat(50);
    let text = "中文".repeat(4500);
    let record = serde_json::json!({"artifact":artifact,"kind":kind,"text":text,"state":"active","state_version":2,"merged_into_id":null});
    let (api, request) = fixture(
        200,
        serde_json::json!({"changed":true,"records":[record]}).to_string(),
    )
    .await;
    let saved = api.remember("scope-a", "synthetic note").await.unwrap();
    assert!(saved.changed);
    assert_eq!(saved.records[0].text, text);
    request.await.unwrap();
    let (api, request) = fixture(200, serde_json::json!({"mode":"text","hits":[{"memory":record,"score":1,"matched_by":["text"]}]}).to_string()).await;
    let searched = api.search("scope-a", "synthetic").await.unwrap();
    assert_eq!(searched.hits[0].artifact, saved.records[0].artifact);
    request.await.unwrap();
    let revision = serde_json::json!({"scope_id":"scope-a","family":"atomic-memory","artifact_id":"atomic/a","revision":7,"content":{"schema":"powercontext.atomic-memory.v1","kind":kind,"text":text},"sources":[{"source_type":"note","source_id":"source-a"}],"artifacts":[],"content_digest":format!("sha256:{}", "0".repeat(64))});
    let (api, request) = fixture(200, revision.to_string()).await;
    let exact = api
        .atomic_entry("scope-a", &saved.records[0].artifact)
        .await
        .unwrap();
    assert_eq!(exact.text, text);
    assert_eq!(exact.kind, kind);
    assert_eq!(exact.source_refs[0].source_id, "source-a");
    assert!(request.await.unwrap().starts_with("GET /proxy/%E4%B8%AD%E6%96%87/v1/scopes/scope-a/artifacts/atomic-memory/atomic%2Fa/revisions/7 "));
    let mut wrong = revision;
    wrong["revision"] = serde_json::json!(8);
    let (api, request) = fixture(200, wrong.to_string()).await;
    assert_eq!(
        api.atomic_entry("scope-a", &saved.records[0].artifact)
            .await
            .err()
            .unwrap()
            .code,
        SafeError::InvalidResponse
    );
    request.await.unwrap();
}

fn atomic_reference() -> ArtifactReference {
    ArtifactReference {
        family: "atomic-memory".into(),
        artifact_id: "atomic/?x".into(),
        revision: 7,
    }
}

fn atomic_revision() -> serde_json::Value {
    serde_json::json!({
        "scope_id":"scope/中文?x", "family":"atomic-memory", "artifact_id":"atomic/?x", "revision":7,
        "content":{"schema":"powercontext.atomic-memory.v1","kind":"note","text":"exact immutable text","creation":{"type":"merge","input_artifact_ids":["input-a","input-b"]}},
        "sources":[{"source_type":"document","source_id":"source 中文"}],
        "artifacts":[{"family":"atomic-memory","artifact_id":"input-a","revision":1}],
        "content_digest":format!("sha256:{}", "a".repeat(64))
    })
}

#[tokio::test]
async fn atomic_exact_read_encodes_every_path_segment_and_preserves_its_reference() {
    let body = atomic_revision();
    let (api, request) = fixture(200, body.to_string()).await;
    let reference = atomic_reference();
    let result = api.atomic_entry("scope/中文?x", &reference).await.unwrap();
    assert_eq!(result.artifact, reference);
    assert_eq!(result.text, "exact immutable text");
    assert_eq!(result.source_refs[0].source_type, "document");
    assert_eq!(result.source_refs[0].source_id, "source 中文");
    assert!(request.await.unwrap().starts_with(
        "GET /proxy/%E4%B8%AD%E6%96%87/v1/scopes/scope%2F%E4%B8%AD%E6%96%87%3Fx/artifacts/atomic-memory/atomic%2F%3Fx/revisions/7 "
    ));
}

#[tokio::test]
async fn atomic_exact_read_rejects_mismatched_identity_without_using_current() {
    for (field, value) in [
        ("scope_id", serde_json::json!("another-scope")),
        ("family", serde_json::json!("skill")),
        ("artifact_id", serde_json::json!("another-artifact")),
        ("revision", serde_json::json!(8)),
    ] {
        let mut body = atomic_revision();
        body[field] = value;
        let (api, request) = fixture(200, body.to_string()).await;
        let error = api
            .atomic_entry("scope/中文?x", &atomic_reference())
            .await
            .err()
            .unwrap();
        assert_eq!(error.code, SafeError::InvalidResponse, "{field}");
        assert!(request.await.unwrap().contains("/revisions/7 "));
    }
}

#[tokio::test]
async fn atomic_write_rejects_inconsistent_merge_state_without_losing_dispatch_uncertainty() {
    for (state, merged_into) in [
        ("merged", serde_json::Value::Null),
        ("active", serde_json::json!("another-memory")),
    ] {
        let body = serde_json::json!({"changed":true,"records":[{
            "artifact":{"family":"atomic-memory","artifact_id":"atomic-a","revision":1},
            "kind":"note","text":"synthetic note","state":state,"state_version":1,"merged_into_id":merged_into
        }]});
        let (api, request) = fixture(200, body.to_string()).await;
        let error = api
            .remember("scope-a", "synthetic note")
            .await
            .err()
            .unwrap();
        assert_eq!(error.code, SafeError::InvalidResponse);
        assert!(error.dispatched);
        request.await.unwrap();
    }
}

#[test]
fn source_locators_preserve_variant_fields_and_a_single_tag_on_roundtrip() {
    use powercontext_desktop::transport::wire::SourceLocator;
    for (wire, sequence) in [
        (r#"{"kind":"sequence","sequence":0}"#, true),
        (
            r#"{"kind":"line_range","start_line":1,"end_line":3,"heading_path":"中文 / Heading"}"#,
            false,
        ),
    ] {
        let locator: SourceLocator = serde_json::from_str(wire).unwrap();
        assert_eq!(matches!(&locator, SourceLocator::Sequence(_)), sequence);
        let encoded = serde_json::to_string(&locator).unwrap();
        assert_eq!(encoded.matches("\"kind\"").count(), 1);
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&encoded).unwrap(),
            serde_json::from_str::<serde_json::Value>(wire).unwrap()
        );
        assert_eq!(
            serde_json::from_str::<SourceLocator>(&encoded).unwrap(),
            locator
        );
    }
}

#[test]
fn source_locators_reject_unknown_or_mismatched_tags() {
    use powercontext_desktop::transport::wire::SourceLocator;
    for wire in [
        r#"{"kind":"future","sequence":0}"#,
        r#"{"kind":"sequence","start_line":1,"end_line":3}"#,
        r#"{"kind":"line_range","sequence":0}"#,
        r#"{"sequence":0}"#,
    ] {
        assert!(
            serde_json::from_str::<SourceLocator>(wire).is_err(),
            "{wire}"
        );
    }
}
