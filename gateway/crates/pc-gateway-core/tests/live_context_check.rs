// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! 对真实 PowerContext Server（PR #2264）的 HTTP 级集成测试。
//!
//! 与单测（FakeGate）互补：这里验证的是 Core 侧加密 binding 的真实行为，
//! mock 无法复现。运行条件：真实 Server 在 PC_TEST_CORE_BASE（默认
//! http://127.0.0.1:8000）可达且配置了 enforcing 保护与 "coding" profile；
//! 不可达时全部跳过（不失败）。

use pc_gateway_core::{GateClient, GateError, HttpGateClient};
use pc_gateway_types::{
    CheckDecision, ContextCheckRequest, ContextCheckResult, ModelJsonPayload,
};
use std::collections::BTreeMap;

const CORE: &str = "http://127.0.0.1:8000";
const TARGET_PROFILE: &str = "coding";
const MODEL_BODY: &str = r#"{"model":"glm-4-flash","max_tokens":80,"stream":false,"messages":[{"role":"user","content":"hi"}]}"#;
const RESPONSE_BODY: &str = r#"{"choices":[{"message":{"role":"assistant","content":"ok"}}],"usage":{"total_tokens":1}}"#;

fn client() -> HttpGateClient {
    HttpGateClient::new(CORE)
}

async fn core_ready() -> bool {
    reqwest::Client::new()
        .get(format!("{CORE}/health/ready"))
        .timeout(std::time::Duration::from_secs(2))
        .send()
        .await
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

async fn require_core() -> Option<()> {
    if core_ready().await {
        Some(())
    } else {
        eprintln!("SKIP: real PowerContext Server not reachable at {CORE}");
        None
    }
}

fn payload(body: &str) -> ModelJsonPayload {
    ModelJsonPayload::new(
        BTreeMap::from([("content-type".to_string(), "application/json".to_string())]),
        body.to_string(),
    )
}

async fn create_scope(tag: &str) -> Option<String> {
    let resp = reqwest::Client::new()
        .post(format!("{CORE}/v1/scopes"))
        .json(&serde_json::json!({
            "title": format!("Rust gateway test {tag}"),
            "summary": "created by live integration tests",
            "idempotency_key": format!("rust-live-{tag}"),
        }))
        .send()
        .await
        .ok()?;
    let v: serde_json::Value = resp.json().await.ok()?;
    v.get("scope_id")?.as_str().map(String::from)
}

async fn before_send(scope: &str) -> Result<ContextCheckResult, GateError> {
    client()
        .check(ContextCheckRequest::BeforeSend {
            scope_id: scope.to_string(),
            target_profile: TARGET_PROFILE.to_string(),
            payload: payload(MODEL_BODY),
            prepared_contexts: Vec::new(),
        })
        .await
}

async fn before_release(scope: &str, binding: &str) -> Result<ContextCheckResult, GateError> {
    client()
        .check(ContextCheckRequest::BeforeRelease {
            scope_id: scope.to_string(),
            target_profile: TARGET_PROFILE.to_string(),
            request_binding: binding.to_string(),
            payload: payload(RESPONSE_BODY),
            completion: pc_gateway_types::CONTEXT_CHECK_COMPLETION.to_string(),
        })
        .await
}

// ---------- 用例 ----------

/// happy path：真实 allow 返回加密 binding、完整覆盖与两个期限。
#[tokio::test]
async fn live_allow_returns_real_binding_and_deadlines() {
    let Some(_) = require_core().await else { return };
    let Some(scope) = create_scope("happy").await else {
        panic!("scope creation failed");
    };
    let result = before_send(&scope).await.expect("check should succeed");

    assert_eq!(result.decision, CheckDecision::Allow, "reasons: {:?}（需配置 coding profile）", result.reason_codes);
    assert!(result.coverage.complete);
    let binding = result.binding.as_deref().expect("allow 必须携带 binding");
    assert!(binding.starts_with("pc1."), "真实加密 binding 前缀: {binding:.20}...");

    // 期限分离：发送窗口 ~60s，关联窗口 ~300s。
    let now = chrono_like_now();
    let valid = parse_epoch_ms(&result.valid_until);
    let correlate = result.correlate_until.as_deref().map(parse_epoch_ms);
    assert!(
        valid > now && valid <= now + 65_000,
        "发送期限应在 60s 窗口内: valid={valid} now={now}"
    );
    let Some(correlate) = correlate else { panic!("before_send allow 必须带 correlate_until") };
    assert!(
        correlate > now && correlate <= now + 310_000,
        "关联期限应在 300s 窗口内"
    );
}

/// 伪造 binding 做 before_release → Core 必须拒绝（绝不能 allow）。
#[tokio::test]
async fn live_forged_binding_is_rejected() {
    let Some(_) = require_core().await else { return };
    let Some(scope) = create_scope("forged").await else { panic!("scope creation failed") };
    let result = before_release(&scope, "pc1.forged-binding-not-issued-by-core").await;
    match result {
        Ok(r) => assert_ne!(
            r.decision,
            CheckDecision::Allow,
            "伪造 binding 不得被放行: {:?}",
            r.reason_codes
        ),
        // 4xx 拒绝（如绑定校验直接失败）同样视为通过。
        Err(GateError::GateUnavailable(msg)) => assert!(
            msg.contains("HTTP 4"),
            "期望 4xx 拒绝，得到: {msg}"
        ),
        Err(other) => panic!("unexpected: {other}"),
    }
}

/// binding 跨 Scope 使用 → 拒绝（对应 PC 测试
/// `test_request_binding_cannot_cross_identity_scope_or_current_policy`）。
#[tokio::test]
async fn live_binding_cannot_cross_scope() {
    let Some(_) = require_core().await else { return };
    let (Some(scope_a), Some(scope_b)) = (create_scope("cross-a").await, create_scope("cross-b").await)
    else {
        panic!("scope creation failed");
    };
    let allow = before_send(&scope_a).await.expect("check a");
    assert_eq!(allow.decision, CheckDecision::Allow, "reasons: {:?}", allow.reason_codes);
    let binding = allow.binding.expect("binding");

    let cross = before_release(&scope_b, &binding).await;
    match cross {
        Ok(r) => assert_ne!(
            r.decision,
            CheckDecision::Allow,
            "A 的 binding 不得放行 B 的释放: {:?}",
            r.reason_codes
        ),
        Err(GateError::GateUnavailable(msg)) => assert!(
            msg.contains("HTTP 4"),
            "期望 4xx 拒绝，得到: {msg}"
        ),
        Err(other) => panic!("unexpected: {other}"),
    }
}

/// 线格式刚性：缺 prepared_contexts（必填）→ 422，验证 Rust DTO 与
/// 真实 OpenAPI 的逐字段对齐。
#[tokio::test]
async fn live_missing_prepared_contexts_rejected() {
    let Some(_) = require_core().await else { return };
    let Some(scope) = create_scope("dto").await else { panic!("scope creation failed") };
    let raw = serde_json::json!({
        "phase": "before_send",
        "scope_id": scope,
        "target_profile": TARGET_PROFILE,
        "payload": {
            "schema": pc_gateway_types::MODEL_JSON_PAYLOAD_SCHEMA,
            "headers": {"content-type": "application/json"},
            "body": MODEL_BODY,
        },
        // 故意省略 prepared_contexts
    });
    let resp = reqwest::Client::new()
        .post(format!("{CORE}/v1/context/check"))
        .json(&raw)
        .send()
        .await
        .expect("post");
    assert_eq!(resp.status().as_u16(), 422, "缺必填字段必须 422");
}

// ---------- 工具 ----------

/// 无 chrono 依赖的简易 RFC3339 解析（足够测试用，格式由 Core 保证）。
fn parse_epoch_ms(rfc3339: &str) -> u64 {
    // 形如 2026-10-28T09:55:06.464580Z
    let bytes = rfc3339.as_bytes();
    let year: i64 = rfc3339[0..4].parse().unwrap();
    let month: i64 = rfc3339[5..7].parse().unwrap();
    let day: i64 = rfc3339[8..10].parse().unwrap();
    let hour: i64 = rfc3339[11..13].parse().unwrap();
    let min: i64 = rfc3339[14..16].parse().unwrap();
    let sec: i64 = rfc3339[17..19].parse().unwrap();
    let millis: i64 = if bytes.len() > 20 {
        let frac = &rfc3339[20..rfc3339.len().saturating_sub(1)];
        let frac = frac.trim_end_matches('Z');
        let taken: String = frac.chars().take(3).collect();
        taken.parse().unwrap_or(0)
    } else {
        0
    };
    // 简化儒略日换算（1970 起，平年近似，测试时间尺度下足够）。
    let days = days_from_civil(year, month, day);
    ((days * 86_400 + hour * 3600 + min * 60 + sec) * 1000 + millis) as u64
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn chrono_like_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
