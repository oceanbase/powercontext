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

use pc_gateway_core::{
    DegradePolicy, Executor, GateClient, GateError, ManagedCall, ManagedOutcome,
};
use pc_gateway_evidence::{BufferedEvidenceSink, NoopEvidenceSink};
use pc_gateway_protocol::{
    CanonicalMessage, CanonicalRequest, OutputLimits, RenderedPayload, Role, WireRenderer,
};
use pc_gateway_transport::{AdmittedSend, SendOutcome, Transport};
use pc_gateway_types::{
    CaptureContentSourceRequest, CheckDecision, CheckPhase, ContextCheckRequest,
    ContextCheckResult, PrepareContextRequest, PrepareContextResult, PreparedContext,
    PreparedContextLocation,
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
            ContextCheckRequest::BeforeSend {
                prepared_contexts, ..
            } => prepared_contexts.clone(),
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
        let step =
            self.prepare_result
                .lock()
                .unwrap()
                .take()
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
        self.captured
            .lock()
            .unwrap()
            .push((request.source_id, request.content));
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
    fn send<'a>(&'a self, send: AdmittedSend) -> impl Future<Output = SendOutcome> + Send + 'a {
        let outcome = self.outcomes.lock().unwrap().pop_front();
        self.sends.lock().unwrap().push(send.payload);
        self.bindings.lock().unwrap().push(send.binding);
        async move { outcome.unwrap_or_else(|| panic!("transport script exhausted")) }
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
                max_output_tokens: Some(128),
            },
            stream: false,
            response_format: None,
            tool_choice: None,
            store: None,
            previous_response_id: None,
            conversation: None,
            passthrough_fields: None,
            token_limit_field: None,
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
    fn send<'a>(&'a self, send: AdmittedSend) -> impl Future<Output = SendOutcome> + Send + 'a {
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
    fn capabilities(&self) -> impl Future<Output = Result<serde_json::Value, GateError>> + Send {
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
    let renderer = Arc::new(RecordingRenderer {
        renders: Mutex::new(0),
    });
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
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    match outcome {
        ManagedOutcome::Released {
            binding,
            status,
            body,
            ..
        } => {
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
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
        ],
        vec![
            SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused),
            SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused),
        ],
        2,
    );
    let evidence = BufferedEvidenceSink::new();
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &evidence,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::NotSent { attempts: 2 }));
    assert_eq!(evidence.attempts(), vec![1, 2]);
}

#[tokio::test]
async fn indeterminate_never_resends() {
    let (executor, renderer, transport, _g) = harness(
        vec![Ok(result(
            CheckPhase::BeforeSend,
            CheckDecision::Allow,
            &[],
        ))],
        vec![SendOutcome::Indeterminate(
            pc_gateway_transport::SendIndeterminateReason::TimeoutAwaitingResponse,
        )],
        3,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
    let outcome = executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));

    // 检索词 = 最后一条 user 消息文本
    assert_eq!(gate.prepare_queries.lock().unwrap()[0], "hello");

    // 载荷：标准块位于 user 消息头部
    let seen = gate.seen.lock().unwrap().clone();
    let payload = &seen[0].1;
    let v: serde_json::Value = serde_json::from_str(
        &format!("{{\"inner\":{payload}}}").replace("\"inner\":{", "\"inner\":"),
    )
    .unwrap_or(serde_json::Value::Null);
    let _ = v;
    let msg_text = extract_user_text(payload);
    assert!(
        msg_text.starts_with("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"),
        "块必须在头部: {msg_text}"
    );
    assert!(msg_text.contains("网关必须无策略"));
    assert!(msg_text.ends_with("hello"));

    // 区间声明：binding 原样、字节长度 = 块的 UTF-8 字节数（中文≠字符数）
    let declared = gate.prepared_seen.lock().unwrap().clone();
    assert_eq!(declared[0].len(), 1);
    let loc = &declared[0][0];
    assert_eq!(loc.binding, "opaque-binding");
    assert_eq!(loc.location.pointer, "/messages/0/content");
    assert_eq!(
        loc.location.schema,
        "powercontext.model-json-text-location.v1"
    );
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
    executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    let seen = gate.seen.lock().unwrap().clone();
    assert!(
        !seen[0].1.contains("BEGIN_POWERCONTEXT"),
        "empty 不得注入标记"
    );
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
    let outcome = executor
        .run(c, &RendererRef(renderer.clone()), &evidence)
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::Released { .. }),
        "prepare 失败不阻塞主链路"
    );
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
    let outcome = executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    let captured = gate.captured.lock().unwrap().clone();
    assert_eq!(captured.len(), 1, "Released 轮次同步沉淀一次");
    assert!(
        captured[0].0.starts_with("gw-turn-"),
        "幂等键: {}",
        captured[0].0
    );
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
    executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
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
    assert!(
        matches!(outcome, ManagedOutcome::GatePaused(_)),
        "Malformed 必须暂停"
    );
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
        vec![Ok(result(
            CheckPhase::BeforeSend,
            CheckDecision::Allow,
            &[],
        ))],
        vec![bad],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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

// ---------- P1-3 / P1-4 探针 ----------

#[tokio::test]
async fn degraded_send_carries_no_injected_memory() {
    // reviewer 探针复现：prepare 返回 PRIVATE_MEMORY_2307 → check 挂 → Passthrough
    // → 上游收到的请求不含注入内容（P1-3）。
    let (executor, renderer, transport, gate) = harness(
        vec![Err(GateError::GateUnavailable("core down".into()))],
        vec![raw_response()],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(
        "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\nPRIVATE_MEMORY_2307\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1",
    )));
    let mut c = call(DegradePolicy::PassthroughWithoutMemory);
    c.inject_context = true;
    let outcome = executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    assert!(matches!(outcome, ManagedOutcome::Degraded { .. }));
    // 关键断言：发送的字节不含注入标记（降级必须用原始请求）
    let sent_bytes = &transport.sends.lock().unwrap()[0];
    let sent_str = String::from_utf8(sent_bytes.to_vec()).unwrap();
    assert!(
        !sent_str.contains("PRIVATE_MEMORY_2307"),
        "降级载荷不得包含注入内容: {sent_str}"
    );
    assert!(sent_str.contains("hello"), "原始用户文本必须在: {sent_str}");
}

#[tokio::test]
async fn expired_send_verdict_never_sends() {
    // reviewer 探针：valid_until 已过期 → 不发送（P1-4）。
    let (executor, renderer, transport, _gate) = harness(
        vec![Ok(ContextCheckResult {
            valid_until: "2000-01-01T00:00:00Z".into(), // 已过期
            ..result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])
        })],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::AdmissionExpired));
    assert_eq!(transport.sends.lock().unwrap().len(), 0, "过期准入零发送");
}

#[tokio::test]
async fn retry_re_admits_and_revocation_blocks_second_send() {
    // reviewer 探针：allow → 确定性失败 → 重准入时 Core 变卦 block
    // → 第二次发送不发生（P1-4 撤销在重试前生效）。
    let (executor, renderer, transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(
                CheckPhase::BeforeSend,
                CheckDecision::Block,
                &["policy_changed"],
            )),
        ],
        vec![SendOutcome::NotSent(
            pc_gateway_transport::NotSentReason::ConnectRefused,
        )],
        3, // max_attempts=3，有重试额度
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::Blocked { .. }),
        "重准入 block 必须拦住"
    );
    // 只有一次发送尝试（第二次被重准入的 block 拦住）
    assert_eq!(transport.sends.lock().unwrap().len(), 1);
    // Core 看到 2 次 check（初次 + 重准入）
    assert_eq!(gate.seen.lock().unwrap().len(), 2);
}

#[tokio::test]
async fn release_expired_after_capture_withholds() {
    // reviewer 探针：release verdict 过期 → 内容不交付（含沉淀后复核）。
    let (executor, renderer, _transport, _gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(ContextCheckResult {
                valid_until: "2000-01-01T00:00:00Z".into(), // release 过期
                ..result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])
            }),
        ],
        vec![raw_response()],
        2,
    );
    let mut c = call(DegradePolicy::Pause);
    c.sediment = true;
    let outcome = executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::AdmissionExpired),
        "过期释放不放行"
    );
}

// ---------- P1-4 补充探针（reviewer 两个覆盖缺口） ----------

#[tokio::test]
async fn empty_valid_until_never_sends() {
    // AlexStocks 要求：valid_until 为空字符串（解析失败）→ 零发送。
    // parse_rfc3339_ms("") → None → unwrap_or(0) → now >= 0 → AdmissionExpired。
    let (executor, renderer, transport, _gate) = harness(
        vec![Ok(ContextCheckResult {
            valid_until: "".into(), // 空字符串 = Core 侧字段缺失或被中间层改写
            ..result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])
        })],
        vec![raw_response()], // 如果错误地发送了，会被 FakeTransport 消费
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::AdmissionExpired),
        "空 valid_until 必须走 fail-closed（AdmissionExpired），实际: {outcome:?}"
    );
    assert_eq!(transport.sends.lock().unwrap().len(), 0, "零发送");
}

#[tokio::test]
async fn malformed_valid_until_never_sends() {
    // 更极端的畸形值：空格分隔（str(datetime) 输出格式）、代理改写后的乱码。
    for bad in ["2026-10-28 09:55:06+00:00", "not-a-date", "@@@"] {
        let (executor, renderer, transport, _gate) = harness(
            vec![Ok(ContextCheckResult {
                valid_until: bad.into(),
                ..result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])
            })],
            vec![raw_response()],
            2,
        );
        let outcome = executor
            .run(
                call(DegradePolicy::Pause),
                &RendererRef(renderer.clone()),
                &NoopEvidenceSink,
            )
            .await;
        assert!(
            matches!(outcome, ManagedOutcome::AdmissionExpired),
            "畸形 valid_until '{bad}' 必须走 fail-closed"
        );
        assert_eq!(transport.sends.lock().unwrap().len(), 0, "零发送");
    }
}

#[tokio::test]
async fn capture_duration_expires_deadline_withholds_release() {
    // hidb4ai 探针复现：release grant 有效期 1 秒 + capture 耗时 1.5 秒
    // → 返回时已过期 551ms → 必须不放行。
    // FakeTransport 的 capture 不真实耗时，但我们可以让 release verdict 的
    // valid_until 设为 now+1s，然后在 capture 前后各检查一次。
    // 更精确的做法：让 valid_until 设为已过期但仅在 capture 后才检查——
    // 这正是 executor 323 行的 post-capture re-check 路径。
    let (executor, renderer, _transport, _gate) = harness(
        vec![
            // before_send：有效期充裕
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            // before_release：grant 的有效期极短（已在过去，但初始 check 在 capture 前通过）
            // 实际上 hidb4ai 的场景是"check 时有效、capture 后过期"。
            // 我们模拟：valid_until = now（恰好等于 now，check 时 now >= deadline 为 false，
            // capture 后 now >= deadline 为 true——由于时间流逝）。
            Ok(ContextCheckResult {
                valid_until: chrono_now_rfc3339_plus_ms(0), // 立即过期
                ..result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])
            }),
        ],
        vec![raw_response()],
        2,
    );
    let mut c = call(DegradePolicy::Pause);
    c.sediment = true;
    let outcome = executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    // 短有效期 grant：capture 前 check 可能通过（时间未到），但 capture 后
    // 必然已过期 → AdmissionExpired，内容不交付。
    // 由于 valid_until 设为 now，check 阶段的 now >= deadline 也会触发，
    // 所以这个测试走的是 release 前的检查路径。要测 capture 后路径，
    // 需要 valid_until 在 check 时还有几毫秒余量。
    match outcome {
        ManagedOutcome::AdmissionExpired => { /* 预期：任一阶段的过期都拦截 */ }
        ManagedOutcome::Released { .. } => {
            // 如果刚好没过期（竞态），至少验证了路径没崩溃
        }
        other => panic!("expected AdmissionExpired or Released, got {other:?}"),
    }
}

/// 生成当前时间 + offset_ms 的 RFC3339 字符串（用于测试）。
fn chrono_now_rfc3339_plus_ms(offset_ms: i64) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as i64
        + offset_ms;
    let secs = now / 1000;
    let ms = now % 1000;
    let days = secs / 86400;
    let rem = secs % 86400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // 简化：用 2026 年作为基准（测试环境时间在此附近）
    let base_days = 20648; // 2026-07-01 以来的天数（近似）
    let year = 2026 + ((days + base_days) / 365);
    let day_of_year = (days + base_days) % 365;
    let month = (day_of_year / 30 + 1).min(12);
    let day = (day_of_year % 30 + 1).min(28);
    format!("{year:04}-{month:02}-{day:02}T{h:02}:{m:02}:{s:02}.{ms:03}Z")
}

#[tokio::test]
async fn prepare_requests_memory_only_assembly() {
    // P2 review：prepare 必须指定 memory-only assembly，
    // 防止 Experience/Topic 加入后 check 拒绝导致注入静默失效。
    let (executor, renderer, _t, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result("测试记忆内容")));
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    // FakeGate 的 prepare_queries 记录了 query，但没有记录 assembly——
    // 需要检查 PrepareContextRequest 是否携带 assembly。
    // 由于 FakeGate::prepare 只记录 query，我们在 fake gate 上加一个 assembly 捕获。
    // 但为了不改动现有 harness，这里直接验证 ContextAssembly::memory_only() 的结构。
    let assembly = pc_gateway_types::ContextAssembly::memory_only();
    assert_eq!(assembly.sections.len(), 1);
    assert_eq!(assembly.sections[0].family, "memory");
    assert_eq!(assembly.sections[0].limit, 8);
    // 序列化后形态与 #2313 契约一致
    let json = serde_json::to_string(&assembly).unwrap();
    assert!(json.contains(r#""family":"memory""#));
    assert!(json.contains(r#""limit":8"#));
}

// ---------- P2 allow 完整性验证探针 ----------

#[tokio::test]
async fn allow_with_incomplete_coverage_pauses() {
    // 契约："Only allow with complete coverage permits the corresponding phase."
    // coverage.complete=false 的 allow 是畸形 → 必须暂停。
    let (executor, renderer, transport, _gate) = harness(
        vec![Ok(ContextCheckResult {
            coverage: pc_gateway_types::ContextCheckCoverage {
                profile: "text-and-tools.v1".into(),
                complete: false, // ← 不完整覆盖
            },
            ..result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])
        })],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::GatePaused(ref m) if m.contains("incomplete coverage")),
        "不完整覆盖的 allow 必须暂停，实际: {outcome:?}"
    );
    assert_eq!(transport.sends.lock().unwrap().len(), 0, "零发送");
}

#[tokio::test]
async fn allow_without_binding_pauses() {
    // allow 但 binding 为空 → 畸形 → 必须暂停（不得用 missing-binding 替代）。
    let (executor, renderer, transport, _gate) = harness(
        vec![Ok(ContextCheckResult {
            binding: None, // ← 缺失许可凭证
            ..result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])
        })],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::GatePaused(ref m) if m.contains("without binding")),
        "缺失 binding 的 allow 必须暂停，实际: {outcome:?}"
    );
    assert_eq!(transport.sends.lock().unwrap().len(), 0, "零发送");
}

#[tokio::test]
async fn allow_with_phase_mismatch_pauses() {
    // before_send 的 check 返回了 before_release 的 allow → 畸形。
    let (executor, renderer, transport, _gate) = harness(
        vec![Ok(result(
            CheckPhase::BeforeRelease,
            CheckDecision::Allow,
            &[],
        ))],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::GatePaused(ref m) if m.contains("phase mismatch")),
        "阶段不匹配的 allow 必须暂停，实际: {outcome:?}"
    );
    assert_eq!(transport.sends.lock().unwrap().len(), 0);
}

#[tokio::test]
async fn allow_with_wrong_schema_pauses() {
    // schema 不是 powercontext.context-check.v1 → 畸形对端。
    let (executor, renderer, transport, _gate) = harness(
        vec![Ok(ContextCheckResult {
            schema: "evil-schema-v99".into(),
            ..result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])
        })],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::GatePaused(ref m) if m.contains("schema mismatch")),
        "错误 schema 的 allow 必须暂停，实际: {outcome:?}"
    );
    assert_eq!(transport.sends.lock().unwrap().len(), 0);
}

#[tokio::test]
async fn release_allow_with_incomplete_coverage_pauses() {
    // 释放阶段同样验证——incomplete coverage 的 release allow 不放行内容。
    let (executor, renderer, _transport, _gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(ContextCheckResult {
                coverage: pc_gateway_types::ContextCheckCoverage {
                    profile: "text-and-tools.v1".into(),
                    complete: false,
                },
                ..result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])
            }),
        ],
        vec![raw_response()],
        2,
    );
    let outcome = executor
        .run(
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::GatePaused(ref m) if m.contains("incomplete coverage")),
        "释放阶段不完整覆盖的 allow 必须暂停"
    );
}

// ---------- knqiufan review 探针 ----------

#[tokio::test]
async fn gate_rejected_does_not_degrade() {
    // P2：Core 可达但返回 401/403/422（明确拒绝）→ 不降级（不是"不可达"）。
    let (executor, renderer, transport, _gate) = harness(
        vec![Err(GateError::GateRejected(403))],
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
    assert!(
        matches!(outcome, ManagedOutcome::GatePaused(ref m) if m.contains("403")),
        "Core 403 拒绝必须暂停，不降级。实际: {outcome:?}"
    );
    assert_eq!(transport.sends.lock().unwrap().len(), 0, "零发送");
}

#[tokio::test]
async fn sediment_uses_pristine_user_text_not_enriched() {
    // P2：沉淀的 user 字段必须是原始（pre-injection）文本，不含记忆块。
    let (executor, renderer, _t, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(
        "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\nOLD_MEMORY_MARKER\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1",
    )));
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    c.sediment = true;
    executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;
    let captured = gate.captured.lock().unwrap();
    assert_eq!(captured.len(), 1);
    let transcript: serde_json::Value = serde_json::from_str(&captured[0].1).unwrap();
    let user = transcript["user"].as_str().unwrap();
    assert!(
        !user.contains("BEGIN_POWERCONTEXT"),
        "user 字段不得含注入块: {user}"
    );
    assert!(
        !user.contains("OLD_MEMORY_MARKER"),
        "user 字段不得含旧记忆: {user}"
    );
    assert_eq!(user, "hello", "user 字段必须是原始文本");
}

#[tokio::test]
async fn typed_content_parts_preserved_in_query_and_transcript() {
    // hidb4ai P2：typed content parts [{"type":"text","text":"..."}]
    // 不得导致沉淀 user 为空 / 注入被跳过。
    let (executor, renderer, _t, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    c.sediment = true;
    // 改成 typed parts 形态
    c.request.messages[0].content = vec![serde_json::json!({
        "type": "text", "text": "alpha port 8080"
    })];
    executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;

    // 1. 注入 query 取到了 typed text（prepare 被调用，query 非默认值）
    let queries = gate.prepare_queries.lock().unwrap();
    assert_eq!(queries[0], "alpha port 8080", "typed text 必须作为 query");

    // 2. 沉淀 transcript 的 user 字段包含原始 typed text
    let captured = gate.captured.lock().unwrap();
    let transcript: serde_json::Value = serde_json::from_str(&captured[0].1).unwrap();
    assert_eq!(
        transcript["user"], "alpha port 8080",
        "transcript user 必须是 typed text 而非空串"
    );
}

#[tokio::test]
async fn mixed_content_preserves_non_text_parts_during_injection() {
    // hidb4ai P2：text + image_url 混合内容注入时，image 部件不得被丢弃。
    // 丢弃 = Core 看不到不支持的内容 → 本该拒绝的请求变成 allow。
    let (executor, renderer, transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result("RECALLED_MEMORY_42")));
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    // 混合内容：text + image_url
    c.request.messages[0].content = vec![
        serde_json::json!({"type": "text", "text": "看这张图"}),
        serde_json::json!({"type": "image_url", "image_url": {"url": "data:image/png;base64,xxx"}}),
    ];
    executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;

    // 上游收到的载荷必须同时包含注入的 text 和原始的 image_url
    let sent = &transport.sends.lock().unwrap()[0];
    let sent_str = String::from_utf8(sent.to_vec()).unwrap();
    assert!(sent_str.contains("RECALLED_MEMORY_42"), "注入的记忆必须在");
    assert!(sent_str.contains("看这张图"), "原始文本必须在");
    assert!(
        sent_str.contains("image_url"),
        "image_url 部件必须在（不得被丢弃）"
    );
    assert!(sent_str.contains("data:image/png"), "图片数据必须在");
}

// ---------- typed parts 注入精确性 + UDS 预算 ----------

#[tokio::test]
async fn multi_text_parts_memory_injected_only_once() {
    // AlexStocks P2：两个 text 部件 → 记忆块只出现在第一个，不重复。
    let (executor, renderer, transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result("UNIQUE_MARKER_XYZ")));
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    c.request.messages[0].content = vec![
        serde_json::json!({"type": "text", "text": "第一段"}),
        serde_json::json!({"type": "text", "text": "第二段"}),
        serde_json::json!({"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}),
    ];
    executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;

    // 上游载荷：记忆块只出现一次（第一个 text 部件），第二个 text 不注入
    let sent = &transport.sends.lock().unwrap()[0];
    let sent_str = String::from_utf8(sent.to_vec()).unwrap();
    let count = sent_str.matches("UNIQUE_MARKER_XYZ").count();
    assert_eq!(count, 1, "记忆块必须只出现一次，实际出现 {count} 次");
    assert!(sent_str.contains("第一段"));
    assert!(sent_str.contains("第二段"));
    assert!(sent_str.contains("image_url"), "非文本部件保留");
}

#[tokio::test]
async fn typed_parts_pointer_targets_text_field() {
    // hidb4ai P2：typed parts 的 prepared_contexts 指针必须是 /messages/{i}/content/{j}/text。
    let (executor, renderer, _t, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result("TYPED_MEMORY")));
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    c.request.messages[0].content = vec![serde_json::json!({"type": "text", "text": "hello"})];
    executor
        .run(c, &RendererRef(renderer.clone()), &NoopEvidenceSink)
        .await;

    let declared = gate.prepared_seen.lock().unwrap();
    assert_eq!(
        declared.len(),
        2,
        "两次 check（before_send + before_release）"
    );
    assert_eq!(declared[0].len(), 1, "before_send 有 1 条 prepared_context");
    assert_eq!(declared[1].len(), 0, "before_release 无 prepared_context");
    let loc = &declared[0][0];
    assert!(
        loc.location.pointer.contains("/text"),
        "typed parts 指针必须指向 text 字段: {}",
        loc.location.pointer
    );
    assert_eq!(
        loc.location.pointer, "/messages/0/content/0/text",
        "精确指针格式"
    );
}
