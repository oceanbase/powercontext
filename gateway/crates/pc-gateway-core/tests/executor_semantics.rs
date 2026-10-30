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

//! PR #2264 失败语义表的状态机级验证（真实契约形态）。

use pc_gateway_core::{DegradePolicy, Executor, GateClient, GateError, ManagedCall, ManagedOutcome};
use pc_gateway_evidence::{BufferedEvidenceSink, NoopEvidenceSink};
use pc_gateway_protocol::{
    CanonicalMessage, CanonicalRequest, OutputLimits, RenderedPayload, Role, WireRenderer,
};
use pc_gateway_transport::{AdmittedSend, SendOutcome, Transport};
use pc_gateway_types::{
    CheckDecision, CheckPhase, ContextCheckRequest, ContextCheckResult, PrepareContextRequest,
    CaptureContentSourceRequest, PrepareContextResult, PreparedContext, PreparedContextLocation,
};
use std::collections::VecDeque;
use std::future::Future;
use std::num::NonZeroU32;
use std::sync::{Arc, Mutex};

// ---------- 测试替身 ----------

struct RecordingRenderer {
    renders: Mutex<u32>,
}

impl WireRenderer for RecordingRenderer {
    fn name(&self) -> &'static str {
        "test"
    }
    fn render(
        &self,
        request: &CanonicalRequest,
        _plan: Option<&serde_json::Value>,
    ) -> Result<RenderedPayload, pc_gateway_protocol::RenderError> {
        *self.renders.lock().unwrap() += 1;
        let messages: Vec<serde_json::Value> = request
            .messages
            .iter()
            .map(|m| {
                let content = match m.content.as_slice() {
                    [serde_json::Value::String(s)] => serde_json::Value::String(s.clone()),
                    other => serde_json::json!(other),
                };
                serde_json::json!({ "role": m.role, "content": content })
            })
            .collect();
        let bytes = serde_json::to_vec(&serde_json::json!({
            "model": request.model,
            "messages": messages,
        }))
        .unwrap()
        .into();
        Ok(RenderedPayload {
            bytes,
            content_type: "application/json",
        })
    }
}

/// 脚本化检查客户端：按序弹出结果，记录请求阶段与载荷。
struct FakeGate {
    results: Mutex<VecDeque<Result<ContextCheckResult, GateError>>>,
    seen: Mutex<Vec<(CheckPhase, String)>>,
    prepared_seen: Mutex<Vec<Vec<PreparedContextLocation>>>,
    prepare_result: Mutex<Option<Result<PrepareContextResult, GateError>>>,
    prepare_queries: Mutex<Vec<String>>,
    captured: Mutex<Vec<(String, String)>>,
}

impl GateClient for FakeGate {
    fn check(
        &self,
        request: ContextCheckRequest,
    ) -> impl Future<Output = Result<ContextCheckResult, GateError>> + Send {
        let (phase, note) = match &request {
            ContextCheckRequest::BeforeSend { payload, .. } => {
                (CheckPhase::BeforeSend, payload.body.clone())
            }
            ContextCheckRequest::BeforeRelease {
                request_binding, ..
            } => (CheckPhase::BeforeRelease, request_binding.clone()),
        };
        let declared = match &request {
            ContextCheckRequest::BeforeSend { prepared_contexts, .. } => prepared_contexts.clone(),
            _ => Vec::new(),
        };
        self.prepared_seen.lock().unwrap().push(declared);
        self.seen.lock().unwrap().push((phase, note));
        let step = self.results.lock().unwrap().pop_front();
        async move { step.expect("gate script exhausted") }
    }

    async fn capabilities(&self) -> Result<serde_json::Value, GateError> {
        Ok(serde_json::json!({ "enforcing": false }))
    }

    fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> impl Future<Output = Result<PrepareContextResult, GateError>> + Send {
        self.prepare_queries.lock().unwrap().push(request.query);
        let step = self.prepare_result.lock().unwrap().take()
            .unwrap_or(Ok(PrepareContextResult::Plain(PreparedContext {
                schema: "powercontext.prepared-context.v1".into(),
                status: "empty".into(),
                content: None,
                content_bytes: 0,
            })));
        async move { step }
    }

    async fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> Result<serde_json::Value, GateError> {
        self.captured.lock().unwrap().push((request.source_id, request.content));
        Ok(serde_json::json!({"captured": true}))
    }
}

struct FakeTransport {
    outcomes: Mutex<VecDeque<SendOutcome>>,
    sends: Mutex<Vec<bytes::Bytes>>,
    bindings: Mutex<Vec<String>>,
    cancels: Mutex<u32>,
}

impl Transport for FakeTransport {
    fn send<'a>(
        &'a self,
        send: AdmittedSend,
    ) -> impl Future<Output = SendOutcome> + Send + 'a {
        let outcome = self.outcomes.lock().unwrap().pop_front();
        self.sends.lock().unwrap().push(send.payload);
        self.bindings.lock().unwrap().push(send.binding);
        async move {
            outcome.unwrap_or_else(|| panic!("transport script exhausted"))
        }
    }

    async fn cancel(&self, _send: &AdmittedSend) {
        *self.cancels.lock().unwrap() += 1;
    }
}

fn raw_response() -> SendOutcome {
    SendOutcome::Delivered(pc_gateway_transport::RawResponse {
        status: 200,
        content_type: "application/json".into(),
        ttft_ms: None,
        body: bytes::Bytes::from_static(br#"{"choices":[{"message":{"role":"assistant","content":"ok"}}],"usage":{"total_tokens":1}}"#),
        usage: Some(serde_json::json!({"total_tokens": 1})),
    })
}

fn result(phase: CheckPhase, decision: CheckDecision, reasons: &[&str]) -> ContextCheckResult {
    ContextCheckResult {
        schema: pc_gateway_types::CONTEXT_CHECK_SCHEMA.into(),
        phase,
        decision,
        reason_codes: reasons.iter().map(|s| s.to_string()).collect(),
        coverage: pc_gateway_types::ContextCheckCoverage {
            profile: "text-and-tools.v1".into(),
            complete: true,
        },
        policy_version: "v1".into(),
        valid_until: "2999-01-01T00:00:00Z".into(),
        correlate_until: None,
        binding: matches!(decision, CheckDecision::Allow).then(|| "binding-1".to_string()),
    }
}

fn call(degrade: DegradePolicy) -> ManagedCall {
    ManagedCall {
        scope_id: "project:test".into(),
        target_profile: "openai:test".into(),
        request: CanonicalRequest {
            model: "test-model".into(),
            messages: vec![CanonicalMessage {
                role: Role::User,
                content: vec![serde_json::json!("hello")],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
            }],
            tools: vec![],
            instructions: None,
            native_cache_fields: None,
            output_limits: OutputLimits {
                max_output_tokens: 128,
            },
            stream: false,
        },
        target_url: "http://upstream.invalid/v1/chat/completions".into(),
        headers: vec![("authorization".into(), "Bearer secret".into())],
        degrade_policy: degrade,
        inject_context: false,
        sediment: false,
        prepared_contexts: Vec::new(),
    }
}

#[derive(Clone)]
struct RendererRef(Arc<RecordingRenderer>);
impl WireRenderer for RendererRef {
    fn name(&self) -> &'static str {
        "test"
    }
    fn render(
        &self,
        request: &CanonicalRequest,
        plan: Option<&serde_json::Value>,
    ) -> Result<RenderedPayload, pc_gateway_protocol::RenderError> {
        WireRenderer::render(&*self.0, request, plan)
    }
}

#[derive(Clone)]
struct TransportRef(Arc<FakeTransport>);
impl Transport for TransportRef {
    fn send<'a>(
        &'a self,
        send: AdmittedSend,
    ) -> impl Future<Output = SendOutcome> + Send + 'a {
        Transport::send(&*self.0, send)
    }
    async fn cancel(&self, send: &AdmittedSend) {
        Transport::cancel(&*self.0, send).await
    }
}

#[derive(Clone)]
struct FakeGateRef(Arc<FakeGate>);
impl GateClient for FakeGateRef {
    fn check(
        &self,
        request: ContextCheckRequest,
    ) -> impl Future<Output = Result<ContextCheckResult, GateError>> + Send {
        GateClient::check(&*self.0, request)
    }
    fn capabilities(
        &self,
    ) -> impl Future<Output = Result<serde_json::Value, GateError>> + Send {
        GateClient::capabilities(&*self.0)
    }

    fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> impl Future<Output = Result<PrepareContextResult, GateError>> + Send {
        GateClient::prepare(&*self.0, request)
    }

    fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> impl Future<Output = Result<serde_json::Value, GateError>> + Send {
        GateClient::capture(&*self.0, request)
    }
}

fn harness(
    gate_results: Vec<Result<ContextCheckResult, GateError>>,
    transport_outcomes: Vec<SendOutcome>,
    max_attempts: u32,
) -> (
    Executor<FakeGateRef, TransportRef>,
    Arc<RecordingRenderer>,
    Arc<FakeTransport>,
    Arc<FakeGate>,
) {
    let renderer = Arc::new(RecordingRenderer { renders: Mutex::new(0) });
    let transport = Arc::new(FakeTransport {
        outcomes: Mutex::new(transport_outcomes.into()),
        sends: Mutex::new(Vec::new()),
        bindings: Mutex::new(Vec::new()),
        cancels: Mutex::new(0),
    });
    let gate = Arc::new(FakeGate {
        results: Mutex::new(gate_results.into()),
        seen: Mutex::new(Vec::new()),
        prepared_seen: Mutex::new(Vec::new()),
        prepare_result: Mutex::new(None),
        prepare_queries: Mutex::new(Vec::new()),
        captured: Mutex::new(Vec::new()),
    });
    let executor = Executor::new(
        FakeGateRef(gate.clone()),
        TransportRef(transport.clone()),
        NonZeroU32::new(max_attempts).unwrap(),
    );
    (executor, renderer, transport, gate)
}

// ---------- 用例 ----------

#[tokio::test]
async fn allow_send_allow_release() {
    let (executor, renderer, transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    match outcome {
        ManagedOutcome::Released { binding, status, body, .. } => {
            assert_eq!(binding.as_deref(), Some("binding-1"));
            assert_eq!(status, 200);
            let v: serde_json::Value = serde_json::from_slice(&body).unwrap();
            assert_eq!(v["choices"][0]["message"]["content"], "ok");
        }
        other => panic!("expected Released, got {other:?}"),
    }
    let seen = gate.seen.lock().unwrap().clone();
    assert_eq!(seen.len(), 2);
    assert_eq!(seen[1], (CheckPhase::BeforeRelease, "binding-1".into()));
    assert_eq!(transport.bindings.lock().unwrap()[0], "binding-1");
}

#[tokio::test]
async fn before_send_block_never_sends() {
    let (executor, renderer, transport, _g) = harness(
        vec![Ok(result(
            CheckPhase::BeforeSend,
            CheckDecision::Block,
            &["privacy.denied"],
        ))],
        vec![],
        2,
    );
    let outcome = executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    match outcome {
        ManagedOutcome::Blocked { reason_codes } => assert_eq!(reason_codes, ["privacy.denied"]),
        other => panic!("expected Blocked, got {other:?}"),
    }
    assert_eq!(transport.sends.lock().unwrap().len(), 0);
}

#[tokio::test]
async fn withhold_is_terminal_in_pr2264_contract() {
    let (executor, renderer, transport, gate) = harness(
        vec![Ok(result(
            CheckPhase::BeforeSend,
            CheckDecision::Withhold,
            &["coverage.incomplete"],
        ))],
        vec![],
        2,
    );
    let outcome = executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    assert!(matches!(outcome, ManagedOutcome::Withheld { .. }));
    assert_eq!(*renderer.renders.lock().unwrap(), 1);
    assert_eq!(gate.seen.lock().unwrap().len(), 1);
    assert_eq!(transport.sends.lock().unwrap().len(), 0);
}

#[tokio::test]
async fn gate_unavailable_pauses_never_allow() {
    let (executor, renderer, transport, _g) = harness(
        vec![Err(GateError::GateUnavailable("down".into()))],
        vec![],
        2,
    );
    let outcome = executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    assert!(matches!(outcome, ManagedOutcome::GatePaused(_)));
    assert_eq!(transport.sends.lock().unwrap().len(), 0);
}

#[tokio::test]
async fn gate_unavailable_degrades_when_allowed() {
    let (executor, renderer, transport, _g) = harness(
        vec![Err(GateError::GateUnavailable("down".into()))],
        vec![raw_response()],
        2,
    );
    let evidence = BufferedEvidenceSink::new();
    let outcome = executor
        .run(
            call(DegradePolicy::PassthroughWithoutMemory),
            &RendererRef(renderer.clone()),
            &evidence,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Degraded { .. }));
    assert_eq!(evidence.not_sent().len(), 1);
    assert_eq!(transport.sends.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn block_does_not_degrade() {
    let (executor, renderer, transport, _g) = harness(
        vec![Ok(result(
            CheckPhase::BeforeSend,
            CheckDecision::Block,
            &["privacy.denied"],
        ))],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::PassthroughWithoutMemory),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Blocked { .. }));
    assert_eq!(transport.sends.lock().unwrap().len(), 0);
}

#[tokio::test]
async fn deterministic_not_sent_retries_bounded() {
    let (executor, renderer, _transport, _g) = harness(
        vec![Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[]))],
        vec![
            SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused),
            SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused),
        ],
        2,
    );
    let evidence = BufferedEvidenceSink::new();
    let outcome = executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &evidence)
        .await;
    assert!(matches!(outcome, ManagedOutcome::NotSent { attempts: 2 }));
    assert_eq!(evidence.attempts(), vec![1, 2]);
}

#[tokio::test]
async fn indeterminate_never_resends() {
    let (executor, renderer, transport, _g) = harness(
        vec![Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[]))],
        vec![SendOutcome::Indeterminate(
            pc_gateway_transport::SendIndeterminateReason::TimeoutAwaitingResponse,
        )],
        3,
    );
    let outcome = executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    assert!(matches!(outcome, ManagedOutcome::SendIndeterminate));
    assert_eq!(transport.sends.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn before_release_block_blocks_and_cancels() {
    let (executor, renderer, transport, _g) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(
                CheckPhase::BeforeRelease,
                CheckDecision::Block,
                &["privacy.denied"],
            )),
        ],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    match outcome {
        ManagedOutcome::Blocked { reason_codes } => assert_eq!(reason_codes, ["privacy.denied"]),
        other => panic!("expected Blocked, got {other:?}"),
    }
    assert_eq!(*transport.cancels.lock().unwrap(), 1);
}

#[tokio::test]
async fn credentials_never_enter_check_payload() {
    let (executor, renderer, _t, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    let seen = gate.seen.lock().unwrap().clone();
    let (_, before_send_payload) = &seen[0];
    assert!(
        !before_send_payload.contains("secret"),
        "authorization 凭据不得进入 check 载荷: {before_send_payload}"
    );
}

// ---------- 注入用例 ----------

fn reuse_result(content: &str) -> PrepareContextResult {
    PrepareContextResult::WithReuse {
        schema: "powercontext.prepared-context-with-reuse.v1".into(),
        context: PreparedContext {
            schema: "powercontext.prepared-context.v1".into(),
            status: "ready".into(),
            content: Some(content.to_string()),
            content_bytes: content.len() as u64,
        },
        reuse: pc_gateway_types::ContextReuse {
            schema: "powercontext.context-reuse.v1".into(),
            constraints: None,
            expires_at: "2999-01-01T00:00:00Z".into(),
            binding: "opaque-binding".into(),
        },
    }
}

#[tokio::test]
async fn injects_block_and_declares_exact_location() {
    let (executor, renderer, transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(
        "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{\"items\":[{\"content\":\"网关必须无策略\"}]}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1",
    )));
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    let outcome = executor.run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink).await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));

    // 检索词 = 最后一条 user 消息文本
    assert_eq!(gate.prepare_queries.lock().unwrap()[0], "hello");

    // 载荷：标准块位于 user 消息头部
    let seen = gate.seen.lock().unwrap().clone();
    let payload = &seen[0].1;
    let v: serde_json::Value = serde_json::from_str(
        &format!("{{\"inner\":{payload}}}").replace("\"inner\":{", "\"inner\":", ),
    )
    .unwrap_or(serde_json::Value::Null);
    let _ = v;
    let msg_text = extract_user_text(payload);
    assert!(msg_text.starts_with("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"), "块必须在头部: {msg_text}");
    assert!(msg_text.contains("网关必须无策略"));
    assert!(msg_text.ends_with("hello"));

    // 区间声明：binding 原样、字节长度 = 块的 UTF-8 字节数（中文≠字符数）
    let declared = gate.prepared_seen.lock().unwrap().clone();
    assert_eq!(declared[0].len(), 1);
    let loc = &declared[0][0];
    assert_eq!(loc.binding, "opaque-binding");
    assert_eq!(loc.location.pointer, "/messages/0/content");
    assert_eq!(loc.location.schema, "powercontext.model-json-text-location.v1");
    assert_eq!(loc.location.start_byte, 0);
    let block = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{\"items\":[{\"content\":\"网关必须无策略\"}]}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1";
    assert_eq!(loc.location.end_byte as usize, block.len());
    assert_eq!(transport.sends.lock().unwrap().len(), 1);
}

fn extract_user_text(before_send_payload: &str) -> String {
    // FakeGate 记录的即渲染后的请求体 JSON
    let body: serde_json::Value = serde_json::from_str(before_send_payload).unwrap();
    body["messages"][0]["content"].as_str().unwrap().to_string()
}

#[tokio::test]
async fn empty_prepare_injects_nothing() {
    let (executor, renderer, _t, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    // prepare 默认返回 empty（FakeGate 默认值）
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    executor.run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink).await;
    let seen = gate.seen.lock().unwrap().clone();
    assert!(!seen[0].1.contains("BEGIN_POWERCONTEXT"), "empty 不得注入标记");
    assert!(gate.prepared_seen.lock().unwrap()[0].is_empty());
}

#[tokio::test]
async fn prepare_failure_still_sends_without_injection() {
    let (executor, renderer, transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Err(GateError::GateUnavailable("down".into())));
    let evidence = BufferedEvidenceSink::new();
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    let outcome = executor.run(c, &RendererRef(renderer.clone()), &evidence).await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }), "prepare 失败不阻塞主链路");
    assert_eq!(transport.sends.lock().unwrap().len(), 1);
    assert!(gate.prepared_seen.lock().unwrap()[0].is_empty());
    assert!(evidence.not_sent()[0].1.starts_with("prepare_failed"));
}

#[tokio::test]
async fn sediment_captures_released_turn_synchronously() {
    let (executor, renderer, _t, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    let mut c = call(DegradePolicy::Pause);
    c.sediment = true;
    let outcome = executor.run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink).await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    let captured = gate.captured.lock().unwrap().clone();
    assert_eq!(captured.len(), 1, "Released 轮次同步沉淀一次");
    assert!(captured[0].0.starts_with("gw-turn-"), "幂等键: {}", captured[0].0);
    let v: serde_json::Value = serde_json::from_str(&captured[0].1).unwrap();
    assert_eq!(v["user"], "hello");
    assert_eq!(v["model"], "test-model");
}

#[tokio::test]
async fn blocked_turn_never_sediments() {
    let (executor, renderer, _t, gate) = harness(
        vec![Ok(result(
            CheckPhase::BeforeSend,
            CheckDecision::Block,
            &["privacy.denied"],
        ))],
        vec![],
        2,
    );
    let mut c = call(DegradePolicy::Pause);
    c.sediment = true;
    executor.run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink).await;
    assert!(gate.captured.lock().unwrap().is_empty(), "被拦内容不得沉淀");
}

#[tokio::test]
async fn malformed_gate_never_degrades_even_with_passthrough() {
    // Core 可达但返回畸形响应：不是"不可用"，绝不允许降级直通（fail-closed）。
    let (executor, renderer, transport, _g) = harness(
        vec![Err(GateError::Malformed("garbage response".into()))],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::PassthroughWithoutMemory),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::GatePaused(_)), "Malformed 必须暂停");
    assert_eq!(transport.sends.lock().unwrap().len(), 0, "零发送");
}

#[tokio::test]
async fn non_utf8_response_is_never_released() {
    // 上游返回非法 UTF-8 字节：不做有损转换送检（保真缺口），直接不放行。
    let bad = SendOutcome::Delivered(pc_gateway_transport::RawResponse {
        status: 200,
        body: bytes::Bytes::from_static(b"{\"text\":\"\xff\xfe\"}"), // 非法 UTF-8
        content_type: "application/json".into(),
        ttft_ms: None,
        usage: None,
    });
    let (executor, renderer, transport, gate) = harness(
        vec![Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[]))],
        vec![bad],
        2,
    );
    let outcome = executor
        .run(call(DegradePolicy::Pause), &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    match outcome {
        ManagedOutcome::Withheld { reason_codes } => {
            assert_eq!(reason_codes, ["invalid_response_encoding"])
        }
        other => panic!("expected Withheld, got {other:?}"),
    }
    // 只有一次检查（before_send）；非法响应根本不送检、不释放。
    // cancel 不适用：非流式调用响应已完整到达，无进行中的上游工作可取消。
    assert_eq!(gate.seen.lock().unwrap().len(), 1);
    assert_eq!(*transport.cancels.lock().unwrap(), 0);
}
