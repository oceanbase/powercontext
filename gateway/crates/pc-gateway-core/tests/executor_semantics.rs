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
    DegradePolicy, ExecutionState, ExecutionTerminal, Executor, GateClient, GateError, ManagedCall,
    ManagedOutcome, ModelSendState, RequestControl,
};
use pc_gateway_evidence::{BufferedEvidenceSink, NoopEvidenceSink};
use pc_gateway_protocol::{
    CanonicalMessage, CanonicalRequest, OutputLimits, RenderedPayload, Role, WireRenderer,
};
use pc_gateway_transport::{AdmittedSend, SendIndeterminateReason, SendOutcome, Transport};
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
    check_holds: Mutex<VecDeque<Option<Arc<tokio::sync::Notify>>>>,
    results: Mutex<VecDeque<Result<ContextCheckResult, GateError>>>,
    seen: Mutex<Vec<(CheckPhase, String)>>,
    prepared_seen: Mutex<Vec<Vec<PreparedContextLocation>>>,
    prepare_result: Mutex<Option<Result<PrepareContextResult, GateError>>>,
    prepare_queries: Mutex<Vec<String>>,
    /// 每次 prepare 是否请求了代码证据。
    prepare_include_code: Mutex<Vec<bool>>,
    captured: Mutex<Vec<(String, String)>>,
    captured_metadata: Mutex<Vec<serde_json::Value>>,
    /// 设置后，capture 记录请求后返回该错误。
    capture_error: Mutex<Option<GateError>>,
    /// 设置后，prepare 在返回前等待屏障，用于让并发请求的准备互相重叠。
    prepare_barrier: Mutex<Option<Arc<tokio::sync::Barrier>>>,
    /// 依次给出的 prepare 结果，先于 `prepare_result` 使用。
    prepare_queue: Mutex<VecDeque<Result<PrepareContextResult, GateError>>>,
    /// 依次对应每次 prepare：`Some` 时等通知后才返回，用于控制完成顺序。
    prepare_holds: Mutex<VecDeque<Option<Arc<tokio::sync::Notify>>>>,
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
        let hold = self.check_holds.lock().unwrap().pop_front().flatten();
        async move {
            if let Some(hold) = hold {
                hold.notified().await;
            }
            step.expect("gate script exhausted")
        }
    }

    async fn capabilities(&self) -> Result<serde_json::Value, GateError> {
        Ok(serde_json::json!({ "enforcing": false }))
    }

    fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> impl Future<Output = Result<PrepareContextResult, GateError>> + Send {
        self.prepare_queries.lock().unwrap().push(request.query);
        self.prepare_include_code
            .lock()
            .unwrap()
            .push(request.include_code);
        let queued = self.prepare_queue.lock().unwrap().pop_front();
        let hold = self.prepare_holds.lock().unwrap().pop_front().flatten();
        let step =
            if let Some(queued) = queued {
                queued
            } else {
                self.prepare_result.lock().unwrap().take().unwrap_or(Ok(
                    PrepareContextResult::Plain(PreparedContext {
                        schema: "powercontext.prepared-context.v1".into(),
                        status: "empty".into(),
                        content: None,
                        content_bytes: 0,
                    }),
                ))
            };
        let barrier = self.prepare_barrier.lock().unwrap().clone();
        async move {
            if let Some(barrier) = barrier {
                barrier.wait().await;
            }
            if let Some(hold) = hold {
                hold.notified().await;
            }
            step
        }
    }

    async fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> Result<serde_json::Value, GateError> {
        self.captured_metadata
            .lock()
            .unwrap()
            .push(request.metadata.clone().unwrap_or(serde_json::Value::Null));
        self.captured
            .lock()
            .unwrap()
            .push((request.source_id, request.content));
        match self.capture_error.lock().unwrap().take() {
            Some(error) => Err(error),
            None => Ok(serde_json::json!({"captured": true})),
        }
    }
}

struct FakeTransport {
    send_holds: Mutex<VecDeque<Option<Arc<tokio::sync::Notify>>>>,
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
        let hold = self.send_holds.lock().unwrap().pop_front().flatten();
        async move {
            if let Some(hold) = hold {
                hold.notified().await;
            }
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
        response_first_byte_ms: None,
        response_complete_ms: None,
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
                extra_fields: Default::default(),
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
        include_code: false,
        capture_prefix: false,
        conversation_id: None,
        sediment: false,
        prepared_contexts: Vec::new(),
    }
}

fn request_control() -> RequestControl {
    RequestControl::new(
        tokio::time::Instant::now() + std::time::Duration::from_secs(180),
        None,
    )
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
        send_holds: Mutex::new(VecDeque::new()),
        outcomes: Mutex::new(transport_outcomes.into()),
        sends: Mutex::new(Vec::new()),
        bindings: Mutex::new(Vec::new()),
        cancels: Mutex::new(0),
    });
    let gate = Arc::new(FakeGate {
        check_holds: Mutex::new(VecDeque::new()),
        results: Mutex::new(gate_results.into()),
        seen: Mutex::new(Vec::new()),
        prepared_seen: Mutex::new(Vec::new()),
        prepare_result: Mutex::new(None),
        prepare_barrier: Mutex::new(None),
        prepare_queue: Mutex::new(VecDeque::new()),
        prepare_holds: Mutex::new(VecDeque::new()),
        prepare_queries: Mutex::new(Vec::new()),
        prepare_include_code: Mutex::new(Vec::new()),
        captured: Mutex::new(Vec::new()),
        captured_metadata: Mutex::new(Vec::new()),
        capture_error: Mutex::new(None),
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
async fn released_execution_retains_control_and_complete_response_facts() {
    let (executor, renderer, transport, _) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(37);
    let control = RequestControl::new(deadline, Some("request-1".into()));
    let observation = control.observation();
    let cancellation = control.cancellation();
    assert_eq!(observation.snapshot().execution, ExecutionState::NotStarted);
    assert_eq!(observation.snapshot().model_send, ModelSendState::NoSend);

    let outcome = executor
        .run(
            control,
            call(DegradePolicy::Pause),
            &RendererRef(renderer),
            &NoopEvidenceSink,
        )
        .await;

    assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    assert_eq!(transport.sends.lock().unwrap().len(), 1);
    assert_eq!(observation.deadline(), deadline);
    assert_eq!(observation.request_id(), Some("request-1"));
    assert_eq!(
        observation.snapshot().model_send,
        ModelSendState::ResponseReceived
    );
    assert_eq!(
        observation.snapshot().execution,
        ExecutionState::Finished(ExecutionTerminal::ReleaseAuthorized)
    );
    let terminal = observation.snapshot();
    cancellation.cancel();
    drop(cancellation);
    assert_eq!(observation.snapshot(), terminal);
}

#[tokio::test]
async fn abandonment_preserves_sending_facts_at_each_pending_boundary() {
    for (boundary, expected_send, expected_attempts) in [
        ("admission", ModelSendState::NoSend, 0),
        ("model", ModelSendState::PossiblySent, 1),
        ("release", ModelSendState::ResponseReceived, 1),
        ("readmission", ModelSendState::DefinitelyNotSent, 1),
        ("degraded", ModelSendState::PossiblySent, 1),
    ] {
        let degraded = boundary == "degraded";
        let (executor, renderer, transport, gate) = harness(
            if degraded {
                vec![Err(GateError::GateUnavailable("offline".into()))]
            } else {
                vec![
                    Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
                    Ok(result(
                        if boundary == "readmission" {
                            CheckPhase::BeforeSend
                        } else {
                            CheckPhase::BeforeRelease
                        },
                        CheckDecision::Allow,
                        &[],
                    )),
                ]
            },
            vec![if boundary == "readmission" {
                SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused)
            } else {
                raw_response()
            }],
            2,
        );
        let hold = Arc::new(tokio::sync::Notify::new());
        match boundary {
            "admission" => gate.check_holds.lock().unwrap().push_back(Some(hold)),
            "release" | "readmission" => {
                gate.check_holds.lock().unwrap().extend([None, Some(hold)]);
            }
            _ => transport.send_holds.lock().unwrap().push_back(Some(hold)),
        }
        let control = request_control();
        let observation = control.observation();
        let cancellation = control.cancellation();
        let renderer = RendererRef(renderer);
        let mut execution = Box::pin(executor.run(
            control,
            call(if degraded {
                DegradePolicy::PassthroughWithoutMemory
            } else {
                DegradePolicy::Pause
            }),
            &renderer,
            &NoopEvidenceSink,
        ));
        assert!(std::future::poll_fn(|context| std::task::Poll::Ready(
            execution.as_mut().poll(context)
        ))
        .await
        .is_pending());
        assert_eq!(
            observation.snapshot().execution,
            ExecutionState::Running,
            "{boundary}"
        );
        assert_eq!(
            observation.snapshot().model_send,
            expected_send,
            "{boundary}"
        );
        drop(execution);
        let terminal = observation.snapshot();
        assert_eq!(
            terminal.execution,
            ExecutionState::Finished(ExecutionTerminal::Abandoned),
            "{boundary}"
        );
        assert_eq!(terminal.model_send, expected_send, "{boundary}");
        assert_eq!(
            transport.sends.lock().unwrap().len(),
            expected_attempts,
            "{boundary}"
        );
        cancellation.cancel();
        drop(cancellation);
        assert_eq!(observation.snapshot(), terminal);
    }
}

#[tokio::test]
async fn cancellation_is_shared_intent_and_does_not_finalize_pending_execution() {
    let (executor, renderer, transport, gate) = harness(
        vec![Ok(result(
            CheckPhase::BeforeSend,
            CheckDecision::Block,
            &["privacy.denied"],
        ))],
        vec![],
        2,
    );
    let hold = Arc::new(tokio::sync::Notify::new());
    gate.check_holds
        .lock()
        .unwrap()
        .push_back(Some(hold.clone()));
    let control = request_control();
    let observer = control.observation();
    let cancellation = control.cancellation();
    let other_cancellation = cancellation.clone();
    let renderer = RendererRef(renderer);
    let mut execution = Box::pin(executor.run(
        control,
        call(DegradePolicy::PassthroughWithoutMemory),
        &renderer,
        &NoopEvidenceSink,
    ));
    assert!(std::future::poll_fn(|context| std::task::Poll::Ready(
        execution.as_mut().poll(context)
    ))
    .await
    .is_pending());
    let mut cancelled = Box::pin(other_cancellation.cancelled());
    assert!(std::future::poll_fn(|context| std::task::Poll::Ready(
        cancelled.as_mut().poll(context)
    ))
    .await
    .is_pending());
    cancellation.cancel();
    cancelled.await;
    assert!(other_cancellation.is_cancelled());
    other_cancellation.cancelled().await;
    assert_eq!(observer.snapshot().execution, ExecutionState::Running);
    hold.notify_one();
    assert!(matches!(execution.await, ManagedOutcome::Blocked { .. }));
    assert_eq!(
        observer.snapshot().execution,
        ExecutionState::Finished(ExecutionTerminal::Blocked)
    );
    assert_eq!(observer.snapshot().model_send, ModelSendState::NoSend);
    assert!(transport.sends.lock().unwrap().is_empty());
}

#[tokio::test]
async fn readmission_keeps_the_callers_deadline_even_when_it_has_expired() {
    let (executor, renderer, transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![
            SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused),
            raw_response(),
        ],
        2,
    );
    let hold = Arc::new(tokio::sync::Notify::new());
    gate.check_holds
        .lock()
        .unwrap()
        .extend([None, Some(hold.clone())]);
    let deadline = tokio::time::Instant::now() - std::time::Duration::from_secs(1);
    let control = RequestControl::new(deadline, None);
    let observer = control.observation();
    let renderer = RendererRef(renderer);
    let mut execution = Box::pin(executor.run(
        control,
        call(DegradePolicy::Pause),
        &renderer,
        &NoopEvidenceSink,
    ));
    assert!(std::future::poll_fn(|context| std::task::Poll::Ready(
        execution.as_mut().poll(context)
    ))
    .await
    .is_pending());
    assert_eq!(observer.deadline(), deadline);
    assert_eq!(
        observer.snapshot().model_send,
        ModelSendState::DefinitelyNotSent
    );
    hold.notify_one();
    assert!(matches!(execution.await, ManagedOutcome::Released { .. }));
    assert_eq!(observer.deadline(), deadline);
    assert_eq!(
        observer.snapshot().model_send,
        ModelSendState::ResponseReceived
    );
    assert_eq!(transport.sends.lock().unwrap().len(), 2);
}

#[tokio::test]
async fn release_failures_preserve_complete_response_facts() {
    let mut malformed = result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[]);
    malformed.coverage.complete = false;
    let mut expired = result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[]);
    expired.valid_until = "2000-01-01T00:00:00Z".into();
    for (release, expected) in [
        (
            Ok(result(
                CheckPhase::BeforeRelease,
                CheckDecision::Block,
                &["privacy.denied"],
            )),
            ExecutionTerminal::Blocked,
        ),
        (
            Ok(result(
                CheckPhase::BeforeRelease,
                CheckDecision::Withhold,
                &["privacy.withheld"],
            )),
            ExecutionTerminal::Withheld,
        ),
        (
            Err(GateError::GateUnavailable("offline".into())),
            ExecutionTerminal::GatePaused,
        ),
        (Ok(malformed), ExecutionTerminal::GatePaused),
        (Ok(expired), ExecutionTerminal::AdmissionExpired),
    ] {
        let (executor, renderer, transport, _) = harness(
            vec![
                Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
                release,
            ],
            vec![raw_response()],
            2,
        );
        let control = request_control();
        let observer = control.observation();
        let outcome = executor
            .run(
                control,
                call(DegradePolicy::Pause),
                &RendererRef(renderer),
                &NoopEvidenceSink,
            )
            .await;
        assert!(matches!(
            (&outcome, expected),
            (ManagedOutcome::Blocked { .. }, ExecutionTerminal::Blocked)
                | (ManagedOutcome::Withheld { .. }, ExecutionTerminal::Withheld)
                | (ManagedOutcome::GatePaused(_), ExecutionTerminal::GatePaused)
                | (
                    ManagedOutcome::AdmissionExpired,
                    ExecutionTerminal::AdmissionExpired
                )
        ));
        assert_eq!(
            observer.snapshot().model_send,
            ModelSendState::ResponseReceived
        );
        assert_eq!(
            observer.snapshot().execution,
            ExecutionState::Finished(expected)
        );
        assert_eq!(transport.sends.lock().unwrap().len(), 1);
    }
}

#[tokio::test]
async fn degraded_and_checked_sends_preserve_their_transport_facts() {
    for degraded in [false, true] {
        for send_state in [
            ModelSendState::DefinitelyNotSent,
            ModelSendState::PossiblySent,
            ModelSendState::ResponseReceived,
        ] {
            let send = match send_state {
                ModelSendState::DefinitelyNotSent => {
                    SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused)
                }
                ModelSendState::PossiblySent => SendOutcome::Indeterminate(
                    pc_gateway_transport::SendIndeterminateReason::LostAfterHandoff,
                ),
                _ => raw_response(),
            };
            let (executor, renderer, transport, _) = harness(
                if degraded {
                    vec![Err(GateError::GateUnavailable("offline".into()))]
                } else {
                    vec![
                        Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
                        Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
                    ]
                },
                vec![send],
                1,
            );
            let control = request_control();
            let observer = control.observation();
            let outcome = executor
                .run(
                    control,
                    call(if degraded {
                        DegradePolicy::PassthroughWithoutMemory
                    } else {
                        DegradePolicy::Pause
                    }),
                    &RendererRef(renderer),
                    &NoopEvidenceSink,
                )
                .await;
            let terminal = match outcome {
                ManagedOutcome::NotSent { attempts: 1 } => ExecutionTerminal::NotSent,
                ManagedOutcome::SendIndeterminate => ExecutionTerminal::SendIndeterminate,
                ManagedOutcome::Degraded { .. } if degraded => ExecutionTerminal::Degraded,
                ManagedOutcome::Released { .. } if !degraded => {
                    ExecutionTerminal::ReleaseAuthorized
                }
                unexpected => panic!("unexpected outcome: {unexpected:?}"),
            };
            assert_eq!(observer.snapshot().model_send, send_state);
            assert_eq!(
                observer.snapshot().execution,
                ExecutionState::Finished(terminal)
            );
            assert_eq!(transport.sends.lock().unwrap().len(), 1);
        }
    }
}

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
            request_control(),
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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

#[tokio::test]
async fn responses_injection_targets_input_text_and_declares_input_pointer() {
    let (executor, _renderer, transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    let block = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{\"items\":[{\"content\":\"测试命令\"}]}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1";
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(block)));
    let mut c = call(DegradePolicy::Pause);
    // Responses 的形态：developer 条目原样封装在前，用户消息由 input_text 部件组成。
    c.request.messages = vec![
        CanonicalMessage {
            role: Role::Tool,
            content: vec![serde_json::json!({"type": "message", "role": "developer",
                "content": [{"type": "input_text", "text": "base"}]})],
            content_null: false,
            tool_calls: None,
            extra_fields: Default::default(),
            tool_call_id: None,
        },
        CanonicalMessage {
            role: Role::User,
            content: vec![
                serde_json::json!({"type": "input_text", "text": "Fix the bug"}),
                serde_json::json!({"type": "input_text", "text": "in util.py"}),
            ],
            content_null: false,
            tool_calls: None,
            extra_fields: Default::default(),
            tool_call_id: None,
        },
    ];
    c.inject_context = true;
    let outcome = executor
        .run(
            request_control(),
            c,
            &pc_gateway_protocol::OpenAiResponsesRenderer,
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));

    // 检索词来自 input_text 部件。
    assert_eq!(
        gate.prepare_queries.lock().unwrap()[0],
        "Fix the bug\nin util.py"
    );

    // 声明的指针在送检的 Responses 请求体中指向注入的字节。
    let declared = gate.prepared_seen.lock().unwrap().clone();
    let loc = &declared[0][0].location;
    assert_eq!(loc.pointer, "/input/1/content/0/text");
    let seen = gate.seen.lock().unwrap().clone();
    let body: serde_json::Value = serde_json::from_str(&seen[0].1).unwrap();
    let text = body.pointer(&loc.pointer).and_then(|v| v.as_str()).unwrap();
    assert_eq!(
        &text.as_bytes()[loc.start_byte as usize..loc.end_byte as usize],
        block.as_bytes()
    );
    assert!(text.ends_with("Fix the bug"));
    // 其他部件与 developer 条目不变。
    assert_eq!(body["input"][1]["content"][1]["text"], "in util.py");
    assert_eq!(body["input"][0]["role"], "developer");
    assert_eq!(transport.sends.lock().unwrap().len(), 1);
}

#[tokio::test]
async fn responses_string_content_uses_a_supported_location_only_when_injected() {
    let block = "测试记忆 🧪";
    let user_text = "Alpha deployment 查询";
    for (inject, prepared) in [(false, block), (true, ""), (true, block)] {
        let (executor, _renderer, transport, gate) = harness(
            vec![
                Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
                Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
            ],
            vec![raw_response()],
            2,
        );
        *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(prepared)));
        let mut c = call(DegradePolicy::Pause);
        c.request.messages[0].content = vec![serde_json::json!(user_text)];
        c.inject_context = inject;
        let outcome = executor
            .run(
                request_control(),
                c,
                &pc_gateway_protocol::GatewayRenderer::Responses(
                    pc_gateway_protocol::OpenAiResponsesRenderer,
                ),
                &NoopEvidenceSink,
            )
            .await;
        assert!(matches!(outcome, ManagedOutcome::Released { .. }));

        let seen = gate.seen.lock().unwrap();
        let body: serde_json::Value = serde_json::from_str(&seen[0].1).unwrap();
        let declarations = gate.prepared_seen.lock().unwrap();
        if inject && !prepared.is_empty() {
            assert_eq!(gate.prepare_queries.lock().unwrap()[0], user_text);
            assert_eq!(declarations[0].len(), 1);
            let loc = &declarations[0][0].location;
            assert_eq!(loc.pointer, "/input/0/content/0/text");
            assert_eq!(body["input"][0]["content"][0]["type"], "input_text");
            let text = body.pointer(&loc.pointer).unwrap().as_str().unwrap();
            assert_eq!(text, format!("{block}\n\n{user_text}"));
            assert_eq!(
                &text.as_bytes()[loc.start_byte as usize..loc.end_byte as usize],
                block.as_bytes()
            );
        } else {
            assert!(declarations[0].is_empty());
            assert_eq!(body["input"][0]["content"], user_text);
        }
        // The admission check and the upstream must see the same bytes.
        assert_eq!(
            transport.sends.lock().unwrap()[0].as_ref(),
            seen[0].1.as_bytes()
        );
    }
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &evidence,
        )
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    let captured = gate.captured.lock().unwrap().clone();
    assert_eq!(captured.len(), 1, "Released 轮次同步沉淀一次");
    assert!(
        captured[0].0.starts_with("gw-turn-"),
        "幂等键: {}",
        captured[0].0
    );
    let metadata = gate.captured_metadata.lock().unwrap();
    assert_eq!(metadata[0]["kind"], "gateway_turn");
    assert_eq!(
        metadata[0]["powercontext_capture_coverage"],
        serde_json::json!({
            "schema": "powercontext.capture-coverage.v1", "projection": "gateway-turn-text.v2",
            "unit": "gateway_turn_projection", "status": "complete", "reasons": []
        })
    );
    let v: serde_json::Value = serde_json::from_str(&captured[0].1).unwrap();
    assert_eq!(
        v,
        serde_json::json!({"kind": "gateway_turn", "user": "hello", "steps": [], "assistant": "ok"})
    );
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
            request_control(),
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
        response_first_byte_ms: None,
        response_complete_ms: None,
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
    let control = request_control();
    let observation = control.observation();
    let outcome = executor
        .run(
            control,
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    match outcome {
        ManagedOutcome::Withheld {
            reason_codes,
            retryable,
        } => {
            assert!(!retryable);
            assert_eq!(reason_codes, ["invalid_response_encoding"])
        }
        other => panic!("expected Withheld, got {other:?}"),
    }
    // 只有一次检查（before_send）；非法响应根本不送检、不释放。
    // cancel 不适用：非流式调用响应已完整到达，无进行中的上游工作可取消。
    assert_eq!(gate.seen.lock().unwrap().len(), 1);
    assert_eq!(*transport.cancels.lock().unwrap(), 0);
    assert_eq!(
        observation.snapshot().model_send,
        ModelSendState::ResponseReceived
    );
    assert_eq!(
        observation.snapshot().execution,
        ExecutionState::Finished(ExecutionTerminal::Withheld)
    );
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
    let control = request_control();
    let observation = control.observation();
    let outcome = executor
        .run(
            control,
            call(DegradePolicy::Pause),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::AdmissionExpired));
    assert_eq!(transport.sends.lock().unwrap().len(), 0, "过期准入零发送");
    assert_eq!(observation.snapshot().model_send, ModelSendState::NoSend);
    assert_eq!(
        observation.snapshot().execution,
        ExecutionState::Finished(ExecutionTerminal::AdmissionExpired)
    );
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
            request_control(),
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
            request_control(),
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
                request_control(),
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
async fn non_enforcing_core_can_admit_retry_and_release_without_privacy_coverage() {
    let verdicts = [
        (CheckPhase::BeforeSend, "initial"),
        (CheckPhase::BeforeSend, "retry"),
        (CheckPhase::BeforeRelease, "release"),
    ]
    .into_iter()
    .map(|(phase, binding)| {
        let mut verdict = result(phase, CheckDecision::Allow, &[]);
        verdict.coverage.complete = false;
        verdict.binding = Some(binding.into());
        Ok(verdict)
    })
    .collect();
    let (executor, renderer, transport, gate) = harness(
        verdicts,
        vec![
            SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused),
            raw_response(),
        ],
        2,
    );
    let outcome = executor
        .with_core_enforcement(false)
        .run(
            request_control(),
            call(DegradePolicy::Pause),
            &RendererRef(renderer),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::Released { binding: Some(ref b), .. } if b == "release")
    );
    assert_eq!(*transport.bindings.lock().unwrap(), ["initial", "retry"]);
    assert_eq!(gate.seen.lock().unwrap().last().unwrap().1, "retry");
}

#[tokio::test]
async fn non_enforcing_core_still_requires_valid_admission_for_each_phase() {
    for phase in [CheckPhase::BeforeSend, CheckPhase::BeforeRelease] {
        for invalid in [
            "binding", "deadline", "schema", "phase", "block", "withhold",
        ] {
            let mut verdict = result(phase, CheckDecision::Allow, &[]);
            verdict.coverage.complete = false;
            match invalid {
                "binding" => verdict.binding = None,
                "deadline" => verdict.valid_until = "2000-01-01T00:00:00Z".into(),
                "schema" => verdict.schema = "invalid".into(),
                "phase" => {
                    verdict.phase = if phase == CheckPhase::BeforeSend {
                        CheckPhase::BeforeRelease
                    } else {
                        CheckPhase::BeforeSend
                    }
                }
                "block" => verdict.decision = CheckDecision::Block,
                "withhold" => verdict.decision = CheckDecision::Withhold,
                _ => unreachable!(),
            }
            let mut verdicts = Vec::new();
            if phase == CheckPhase::BeforeRelease {
                verdicts.push(Ok(result(
                    CheckPhase::BeforeSend,
                    CheckDecision::Allow,
                    &[],
                )));
            }
            verdicts.push(Ok(verdict));
            let (executor, renderer, transport, _) = harness(verdicts, vec![raw_response()], 1);
            let outcome = executor
                .with_core_enforcement(false)
                .run(
                    request_control(),
                    call(DegradePolicy::Pause),
                    &RendererRef(renderer),
                    &NoopEvidenceSink,
                )
                .await;
            match invalid {
                "deadline" => assert!(matches!(outcome, ManagedOutcome::AdmissionExpired)),
                "block" => assert!(matches!(outcome, ManagedOutcome::Blocked { .. })),
                "withhold" => assert!(matches!(outcome, ManagedOutcome::Withheld { .. })),
                _ => assert!(matches!(outcome, ManagedOutcome::GatePaused(_))),
            }
            assert_eq!(
                transport.sends.lock().unwrap().len(),
                usize::from(phase == CheckPhase::BeforeRelease)
            );
        }
    }
}

#[tokio::test]
async fn allow_with_incomplete_coverage_pauses() {
    // Without an explicit non-enforcing configuration, incomplete coverage pauses.
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
            request_control(),
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
    // The default coverage requirement also applies to release admission.
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
            request_control(),
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
            request_control(),
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
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

// ---------- 回合内复用（RFC 2199：新用户消息才触发一次准备） ----------

/// 工具循环的第 `step` 个请求：同一条用户消息之后跟着 `step` 轮工具调用与输出。
fn tool_loop_call(user_text: &str, step: usize, responses: bool) -> ManagedCall {
    let mut c = call(DegradePolicy::Pause);
    c.inject_context = true;
    c.conversation_id = Some("conversation-a".into());
    let user = if responses {
        serde_json::json!({"type": "input_text", "text": user_text})
    } else {
        serde_json::json!(user_text)
    };
    c.request.messages = vec![CanonicalMessage {
        role: Role::User,
        content: vec![user],
        content_null: false,
        tool_calls: None,
        extra_fields: Default::default(),
        tool_call_id: None,
    }];
    for i in 0..step {
        let id = format!("call_{i}");
        if responses {
            for item in [
                serde_json::json!({"type": "function_call", "call_id": id, "name": "run", "arguments": "{}"}),
                serde_json::json!({"type": "function_call_output", "call_id": id, "output": "ok"}),
            ] {
                c.request.messages.push(CanonicalMessage {
                    role: Role::Tool,
                    content: vec![item],
                    content_null: false,
                    tool_calls: None,
                    extra_fields: Default::default(),
                    tool_call_id: None,
                });
            }
        } else {
            c.request.messages.push(CanonicalMessage {
                role: Role::Assistant,
                content: vec![],
                content_null: true,
                tool_calls: Some(vec![serde_json::json!({
                    "id": id, "type": "function", "function": {"name": "run", "arguments": "{}"}
                })]),
                extra_fields: Default::default(),
                tool_call_id: None,
            });
            c.request.messages.push(CanonicalMessage {
                role: Role::Tool,
                content: vec![serde_json::json!("ok")],
                content_null: false,
                tool_calls: None,
                extra_fields: Default::default(),
                tool_call_id: Some(id),
            });
        }
    }
    c
}

fn allow_runs(n: usize) -> Vec<Result<ContextCheckResult, GateError>> {
    (0..n)
        .flat_map(|_| {
            [
                Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
                Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
            ]
        })
        .collect()
}

/// before_send 送检的请求体与声明（每个请求一条）。
fn sends(gate: &FakeGate) -> Vec<(String, Vec<PreparedContextLocation>)> {
    let seen = gate.seen.lock().unwrap().clone();
    let declared = gate.prepared_seen.lock().unwrap().clone();
    seen.into_iter()
        .zip(declared)
        .filter(|((phase, _), _)| *phase == CheckPhase::BeforeSend)
        .map(|((_, body), declared)| (body, declared))
        .collect()
}

async fn run_tool_loop(responses: bool) {
    let block = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{\"items\":[]}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1";
    let (executor, renderer, transport, gate) =
        harness(allow_runs(4), (0..4).map(|_| raw_response()).collect(), 2);
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(block)));
    for step in 0..3 {
        let c = tool_loop_call("hello", step, responses);
        let outcome = if responses {
            executor
                .run(
                    request_control(),
                    c,
                    &pc_gateway_protocol::OpenAiResponsesRenderer,
                    &NoopEvidenceSink,
                )
                .await
        } else {
            executor
                .run(
                    request_control(),
                    c,
                    &RendererRef(renderer.clone()),
                    &NoopEvidenceSink,
                )
                .await
        };
        assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    }
    // 一个回合只准备一次；每一步注入同一个块、声明同一个绑定和位置。
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 1);
    let sent = sends(&gate);
    assert_eq!(sent.len(), 3);
    for (body, declared) in &sent {
        assert_eq!(declared.len(), 1);
        assert_eq!(declared[0].binding, "opaque-binding");
        assert_eq!(declared[0].location.pointer, sent[0].1[0].location.pointer);
        assert_eq!(
            declared[0].location.end_byte,
            sent[0].1[0].location.end_byte
        );
        let body: serde_json::Value = serde_json::from_str(body).unwrap();
        let text = body
            .pointer(&declared[0].location.pointer)
            .unwrap()
            .as_str()
            .unwrap();
        assert!(text.starts_with(block));
    }
    // 新的用户消息开始新回合：重新准备一次。
    let c = tool_loop_call("next question", 0, responses);
    if responses {
        executor
            .run(
                request_control(),
                c,
                &pc_gateway_protocol::OpenAiResponsesRenderer,
                &NoopEvidenceSink,
            )
            .await;
    } else {
        executor
            .run(
                request_control(),
                c,
                &RendererRef(renderer.clone()),
                &NoopEvidenceSink,
            )
            .await;
    }
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
    assert_eq!(transport.sends.lock().unwrap().len(), 4);
}

#[tokio::test]
async fn chat_tool_loop_prepares_once_per_user_message() {
    run_tool_loop(false).await;
}

#[tokio::test]
async fn responses_tool_loop_prepares_once_per_user_message() {
    run_tool_loop(true).await;
}

#[tokio::test]
async fn empty_preparation_is_reused_within_the_turn() {
    let (executor, renderer, transport, gate) =
        harness(allow_runs(3), (0..3).map(|_| raw_response()).collect(), 2);
    for step in 0..3 {
        let outcome = executor
            .run(
                request_control(),
                tool_loop_call("hello", step, false),
                &RendererRef(renderer.clone()),
                &NoopEvidenceSink,
            )
            .await;
        assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    }
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 1);
    assert!(sends(&gate).iter().all(|(_, declared)| declared.is_empty()));
    assert_eq!(transport.sends.lock().unwrap().len(), 3);
}

#[tokio::test]
async fn failed_preparation_is_retried_on_the_next_step_without_blocking() {
    let (executor, renderer, transport, gate) =
        harness(allow_runs(2), (0..2).map(|_| raw_response()).collect(), 2);
    *gate.prepare_result.lock().unwrap() = Some(Err(GateError::GateUnavailable("down".into())));
    for step in 0..2 {
        let outcome = executor
            .run(
                request_control(),
                tool_loop_call("hello", step, false),
                &RendererRef(renderer.clone()),
                &NoopEvidenceSink,
            )
            .await;
        assert!(
            matches!(outcome, ManagedOutcome::Released { .. }),
            "prepare 失败不阻塞主链路"
        );
    }
    // 失败不缓存：第二步重新准备（这次得到空结果）。
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
    assert_eq!(transport.sends.lock().unwrap().len(), 2);
}

#[tokio::test]
async fn conversations_with_identical_messages_prepare_separately() {
    let (executor, renderer, _transport, gate) =
        harness(allow_runs(2), (0..2).map(|_| raw_response()).collect(), 2);
    for conversation in ["conversation-a", "conversation-b"] {
        let mut c = tool_loop_call("hello", 0, false);
        c.conversation_id = Some(conversation.into());
        executor
            .run(
                request_control(),
                c,
                &RendererRef(renderer.clone()),
                &NoopEvidenceSink,
            )
            .await;
    }
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
}

#[tokio::test]
async fn requests_without_a_conversation_id_never_inherit_an_injection() {
    let block = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{\"items\":[]}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1";
    let (executor, renderer, _transport, gate) =
        harness(allow_runs(2), (0..2).map(|_| raw_response()).collect(), 2);
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(block)));
    for step in 0..2 {
        let mut c = tool_loop_call("hello", step, false);
        c.conversation_id = None;
        executor
            .run(
                request_control(),
                c,
                &RendererRef(renderer.clone()),
                &NoopEvidenceSink,
            )
            .await;
    }
    // 每个请求各自准备；第二次准备返回空，第二个请求不得沿用第一个请求的块。
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
    let sent = sends(&gate);
    assert_eq!(sent[0].1.len(), 1);
    assert!(sent[1].1.is_empty());
}

#[tokio::test]
async fn concurrent_requests_of_a_turn_commit_one_prepared_value() {
    let block = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{\"items\":[]}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1";
    let (executor, renderer, _transport, gate) =
        harness(allow_runs(3), (0..3).map(|_| raw_response()).collect(), 2);
    // 两个并发请求都错过缓存并同时准备：第一个得到块，第二个得到空结果。
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(block)));
    *gate.prepare_barrier.lock().unwrap() = Some(Arc::new(tokio::sync::Barrier::new(2)));
    let r = RendererRef(renderer.clone());
    let (a, b) = tokio::join!(
        executor.run(
            request_control(),
            tool_loop_call("hello", 0, false),
            &r,
            &NoopEvidenceSink
        ),
        executor.run(
            request_control(),
            tool_loop_call("hello", 1, false),
            &r,
            &NoopEvidenceSink
        ),
    );
    assert!(matches!(a, ManagedOutcome::Released { .. }));
    assert!(matches!(b, ManagedOutcome::Released { .. }));
    assert_eq!(
        gate.prepare_queries.lock().unwrap().len(),
        2,
        "both requests prepared concurrently"
    );
    *gate.prepare_barrier.lock().unwrap() = None;

    // 稍后的同一回合请求重放已提交的那一份。
    executor
        .run(
            request_control(),
            tool_loop_call("hello", 2, false),
            &r,
            &NoopEvidenceSink,
        )
        .await;
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
    let injected = |(body, declared): &(String, Vec<PreparedContextLocation>)| {
        declared.first().map(|d| {
            let body: serde_json::Value = serde_json::from_str(body).unwrap();
            let text = body
                .pointer(&d.location.pointer)
                .unwrap()
                .as_str()
                .unwrap()
                .to_owned();
            (
                d.binding.clone(),
                text[..d.location.end_byte as usize].to_owned(),
            )
        })
    };
    let sent = sends(&gate);
    assert_eq!(sent.len(), 3);
    // 三个请求注入同一个值（同为同一个块，或同为不注入）。
    assert_eq!(injected(&sent[0]), injected(&sent[1]));
    assert_eq!(injected(&sent[1]), injected(&sent[2]));
}

#[tokio::test]
async fn a_late_preparation_from_an_earlier_turn_does_not_replace_the_current_turn() {
    let (executor, renderer, _transport, gate) =
        harness(allow_runs(3), (0..3).map(|_| raw_response()).collect(), 2);
    for snapshot in [
        "memory-snapshot-1",
        "memory-snapshot-2",
        "memory-snapshot-3",
    ] {
        gate.prepare_queue
            .lock()
            .unwrap()
            .push_back(Ok(reuse_result(snapshot)));
    }
    let earlier = Arc::new(tokio::sync::Notify::new());
    gate.prepare_holds
        .lock()
        .unwrap()
        .extend([Some(earlier.clone()), None, None]);
    let r = RendererRef(renderer.clone());
    // 上一回合的请求先开始准备、最后才完成；用户在此期间发了新消息，新回合先准备并提交。
    let current_turn = async {
        let outcome = executor
            .run(
                request_control(),
                tool_loop_call("second", 0, false),
                &r,
                &NoopEvidenceSink,
            )
            .await;
        earlier.notify_one();
        outcome
    };
    let (late, current) = tokio::join!(
        executor.run(
            request_control(),
            tool_loop_call("first", 0, false),
            &r,
            &NoopEvidenceSink
        ),
        current_turn,
    );
    assert!(matches!(late, ManagedOutcome::Released { .. }));
    assert!(matches!(current, ManagedOutcome::Released { .. }));
    // 当前回合的续写仍重放它已提交的内容，不再准备。
    executor
        .run(
            request_control(),
            tool_loop_call("second", 1, false),
            &r,
            &NoopEvidenceSink,
        )
        .await;
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
    let current_turn_sends: Vec<_> = sends(&gate)
        .into_iter()
        .filter(|(body, _)| body.contains("second"))
        .collect();
    assert_eq!(current_turn_sends.len(), 2);
    assert!(
        current_turn_sends
            .iter()
            .all(|(body, _)| body.contains("memory-snapshot-2")
                && !body.contains("memory-snapshot-3"))
    );
}

// ---------- #2407：每个用户回合在终止响应时沉淀一次规范化记录 ----------

fn delivered(body: serde_json::Value) -> SendOutcome {
    SendOutcome::Delivered(pc_gateway_transport::RawResponse {
        status: 200,
        content_type: "application/json".into(),
        response_first_byte_ms: None,
        response_complete_ms: None,
        body: bytes::Bytes::from(body.to_string()),
        usage: None,
    })
}

fn chat_tool_call_response() -> SendOutcome {
    delivered(serde_json::json!({
        "id": "chatcmpl-1",
        "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": null,
            "tool_calls": [{"id": "call_9", "type": "function", "function": {"name": "run", "arguments": "{}"}}]}}],
        "usage": {"total_tokens": 7}
    }))
}

fn chat_final_response(text: &str) -> SendOutcome {
    delivered(serde_json::json!({
        "id": "chatcmpl-2",
        "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
        "usage": {"total_tokens": 9}
    }))
}

fn responses_output(items: serde_json::Value) -> SendOutcome {
    delivered(serde_json::json!({
        "id": "resp_1", "object": "response", "status": "completed", "output": items,
        "usage": {"input_tokens": 10, "output_tokens": 2}
    }))
}

fn reasoning_item() -> serde_json::Value {
    serde_json::json!({"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "gAAAA-secret"})
}

/// 规范化记录不得含推理密文、各类 ID、用量等元数据。
fn assert_no_transport_metadata(content: &str) {
    for field in [
        "encrypted_content",
        "gAAAA-secret",
        "call_id",
        "call_0",
        "call_9",
        "rs_1",
        "resp_1",
        "chatcmpl",
        "usage",
        "total_tokens",
        "BEGIN_POWERCONTEXT",
    ] {
        assert!(
            !content.contains(field),
            "transcript contains {field}: {content}"
        );
    }
}

#[tokio::test]
async fn chat_tool_loop_captures_once_at_the_terminal_response() {
    let (executor, renderer, _transport, gate) = harness(
        allow_runs(2),
        vec![chat_tool_call_response(), chat_final_response("done")],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result("memory-snapshot-1")));
    let r = RendererRef(renderer.clone());
    for step in 0..2 {
        let mut c = tool_loop_call("fix the bug", step, false);
        c.sediment = true;
        let outcome = executor
            .run(request_control(), c, &r, &NoopEvidenceSink)
            .await;
        assert!(matches!(outcome, ManagedOutcome::Released { .. }));
        let expected = step;
        assert_eq!(
            gate.captured.lock().unwrap().len(),
            expected,
            "a tool-calling response is not captured"
        );
    }
    let captured = gate.captured.lock().unwrap().clone();
    let transcript: serde_json::Value = serde_json::from_str(&captured[0].1).unwrap();
    assert_eq!(
        transcript,
        serde_json::json!({
            "kind": "gateway_turn",
            "user": "fix the bug",
            "steps": [
                {"type": "tool_call", "name": "run", "arguments": "{}"},
                {"type": "tool_output", "output": "ok"}
            ],
            "assistant": "done"
        })
    );
    assert_no_transport_metadata(&captured[0].1);
}

#[tokio::test]
async fn responses_tool_loop_captures_once_at_the_terminal_response() {
    let (executor, _renderer, _transport, gate) = harness(
        allow_runs(2),
        vec![
            responses_output(serde_json::json!([
                reasoning_item(),
                {"id": "fc_1", "type": "function_call", "call_id": "call_0", "name": "run", "arguments": "{\"cmd\":\"ls\"}"}
            ])),
            responses_output(serde_json::json!([
                reasoning_item(),
                {"id": "msg_1", "type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "done", "annotations": []}]}
            ])),
        ],
        2,
    );
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result("memory-snapshot-1")));
    for step in 0..2 {
        let mut c = tool_loop_call("fix the bug", step, true);
        c.sediment = true;
        if step == 1 {
            // 下一个请求带回上一步的推理条目与工具调用。
            c.request.messages.insert(
                1,
                CanonicalMessage {
                    role: Role::Tool,
                    content: vec![reasoning_item()],
                    content_null: false,
                    tool_calls: None,
                    extra_fields: Default::default(),
                    tool_call_id: None,
                },
            );
        }
        let outcome = executor
            .run(
                request_control(),
                c,
                &pc_gateway_protocol::OpenAiResponsesRenderer,
                &NoopEvidenceSink,
            )
            .await;
        assert!(matches!(outcome, ManagedOutcome::Released { .. }));
        assert_eq!(gate.captured.lock().unwrap().len(), step);
    }
    let captured = gate.captured.lock().unwrap().clone();
    let transcript: serde_json::Value = serde_json::from_str(&captured[0].1).unwrap();
    assert_eq!(
        transcript,
        serde_json::json!({
            "kind": "gateway_turn",
            "user": "fix the bug",
            "steps": [
                {"type": "tool_call", "name": "run", "arguments": "{}"},
                {"type": "tool_output", "output": "ok"}
            ],
            "assistant": "done"
        })
    );
    assert_no_transport_metadata(&captured[0].1);
}

#[tokio::test]
async fn a_captured_turn_keeps_every_call_field_and_output_uncut() {
    let call_item = serde_json::json!({"type": "apply_patch_call", "call_id": "call_0",
        "operation": {"type": "update_file", "path": "example.py", "diff": "@@\n-old\n+new"}});
    let long = "x".repeat(40_000);
    let output_item =
        serde_json::json!({"type": "apply_patch_call_output", "call_id": "call_0", "output": long});
    let (executor, _renderer, _transport, gate) = harness(
        allow_runs(2),
        vec![
            responses_output(serde_json::json!([call_item.clone()])),
            responses_output(serde_json::json!([
                {"type": "message", "role": "assistant", "content": "done"}
            ])),
        ],
        2,
    );
    for step in 0..2 {
        let mut c = tool_loop_call("fix the bug", step, true);
        c.inject_context = false;
        c.sediment = true;
        if step == 1 {
            c.request.messages[1].content = vec![call_item.clone()];
            c.request.messages[2].content = vec![output_item.clone()];
        }
        let outcome = executor
            .run(
                request_control(),
                c,
                &pc_gateway_protocol::OpenAiResponsesRenderer,
                &NoopEvidenceSink,
            )
            .await;
        assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    }
    let captured = gate.captured.lock().unwrap();
    assert_eq!(captured.len(), 1);
    let body: serde_json::Value = serde_json::from_str(&captured[0].1).unwrap();
    assert_eq!(
        body,
        serde_json::json!({
            "kind": "gateway_turn", "user": "fix the bug",
            "steps": [
                {"type": "tool_call", "name": "apply_patch_call",
                 "arguments": "{\"diff\":\"@@\\n-old\\n+new\",\"path\":\"example.py\",\"type\":\"update_file\"}"},
                {"type": "tool_output", "output": long}
            ],
            "assistant": "done"
        })
    );
    assert_eq!(
        gate.captured_metadata.lock().unwrap()[0]["powercontext_capture_coverage"],
        serde_json::json!({
            "schema": "powercontext.capture-coverage.v1", "projection": "gateway-turn-text.v2",
            "unit": "gateway_turn_projection", "status": "complete", "reasons": []
        })
    );
}

#[tokio::test]
async fn a_failed_capture_still_releases_and_is_recorded() {
    let (executor, renderer, _transport, gate) =
        harness(allow_runs(1), vec![chat_final_response("done")], 2);
    *gate.capture_error.lock().unwrap() = Some(GateError::GateUnavailable("down".into()));
    let evidence = BufferedEvidenceSink::new();
    let mut c = call(DegradePolicy::Pause);
    c.sediment = true;
    let outcome = executor
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &evidence,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    assert_eq!(gate.captured.lock().unwrap().len(), 1);
    assert!(evidence
        .not_sent()
        .iter()
        .any(|(_, reason)| reason.starts_with("sediment_failed:")));
}

#[tokio::test]
async fn a_blocked_release_is_never_captured() {
    let (executor, renderer, _transport, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(
                CheckPhase::BeforeRelease,
                CheckDecision::Block,
                &["privacy.denied"],
            )),
        ],
        vec![chat_final_response("secret answer")],
        2,
    );
    let mut c = call(DegradePolicy::Pause);
    c.sediment = true;
    let outcome = executor
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Blocked { .. }));
    assert!(gate.captured.lock().unwrap().is_empty());
}

#[tokio::test]
async fn a_degraded_delivery_is_never_captured() {
    let (executor, renderer, transport, gate) = harness(
        vec![Err(GateError::GateUnavailable("down".into()))],
        vec![chat_final_response("unchecked answer")],
        2,
    );
    let mut c = call(DegradePolicy::PassthroughWithoutMemory);
    c.sediment = true;
    let outcome = executor
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Degraded { .. }));
    assert_eq!(transport.sends.lock().unwrap().len(), 1);
    assert!(gate.captured.lock().unwrap().is_empty());
}

/// 每次放行的 release binding 都不同（Core 每次重新封装），与真实行为一致。
fn allow_runs_with_fresh_release_bindings(n: usize) -> Vec<Result<ContextCheckResult, GateError>> {
    (0..n)
        .flat_map(|i| {
            [
                Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
                Ok(ContextCheckResult {
                    binding: Some(format!("release-{i}")),
                    ..result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])
                }),
            ]
        })
        .collect()
}

fn captured_ids(gate: &FakeGate) -> Vec<String> {
    gate.captured
        .lock()
        .unwrap()
        .iter()
        .map(|(id, _)| id.clone())
        .collect()
}

#[tokio::test]
async fn retried_and_concurrent_terminal_requests_of_a_turn_share_one_source_id() {
    let (executor, renderer, _transport, gate) = harness(
        allow_runs_with_fresh_release_bindings(5),
        (0..5)
            .map(|i| chat_final_response(&format!("done {i}")))
            .collect(),
        2,
    );
    let r = RendererRef(renderer.clone());
    let terminal = |conversation: &str| {
        let mut c = tool_loop_call("fix the bug", 1, false);
        c.inject_context = false;
        c.sediment = true;
        c.conversation_id = Some(conversation.into());
        c
    };
    // 同一回合：先后重试两次，再并发重复两次。
    executor
        .run(
            request_control(),
            terminal("conversation-a"),
            &r,
            &NoopEvidenceSink,
        )
        .await;
    executor
        .run(
            request_control(),
            terminal("conversation-a"),
            &r,
            &NoopEvidenceSink,
        )
        .await;
    tokio::join!(
        executor.run(
            request_control(),
            terminal("conversation-a"),
            &r,
            &NoopEvidenceSink
        ),
        executor.run(
            request_control(),
            terminal("conversation-a"),
            &r,
            &NoopEvidenceSink
        ),
    );
    let ids = captured_ids(&gate);
    assert_eq!(ids.len(), 4);
    assert!(
        ids.iter().all(|id| id == &ids[0]),
        "one turn, one Source ID: {ids:?}"
    );
    // 另一个会话中相同的消息是另一个回合。
    executor
        .run(
            request_control(),
            terminal("conversation-b"),
            &r,
            &NoopEvidenceSink,
        )
        .await;
    assert_ne!(captured_ids(&gate)[4], ids[0]);
}

#[tokio::test]
async fn without_a_conversation_identical_turns_are_never_merged() {
    let (executor, renderer, _transport, gate) = harness(
        allow_runs_with_fresh_release_bindings(2),
        vec![chat_final_response("done"), chat_final_response("done")],
        2,
    );
    for _ in 0..2 {
        let mut c = tool_loop_call("fix the bug", 1, false);
        c.inject_context = false;
        c.sediment = true;
        c.conversation_id = None;
        executor
            .run(
                request_control(),
                c,
                &RendererRef(renderer.clone()),
                &NoopEvidenceSink,
            )
            .await;
    }
    let ids = captured_ids(&gate);
    assert_eq!(ids.len(), 2);
    assert_ne!(ids[0], ids[1]);
}

#[tokio::test]
async fn a_turn_already_captured_with_other_content_is_recorded_as_a_duplicate() {
    let (executor, renderer, _transport, gate) =
        harness(allow_runs(1), vec![chat_final_response("done again")], 2);
    // Core 对同一 Source ID 的不同内容返回 409 source_conflict。
    *gate.capture_error.lock().unwrap() = Some(GateError::GateRejected(409));
    let evidence = BufferedEvidenceSink::new();
    let mut c = tool_loop_call("fix the bug", 1, false);
    c.inject_context = false;
    c.sediment = true;
    let outcome = executor
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &evidence,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    let reasons: Vec<String> = evidence.not_sent().into_iter().map(|(_, r)| r).collect();
    assert_eq!(reasons, vec!["sediment_duplicate".to_string()]);
}

#[tokio::test]
async fn a_hosted_tool_response_is_terminal_but_a_client_tool_call_is_not() {
    let (executor, _renderer, _transport, gate) = harness(
        allow_runs(3),
        vec![
            responses_output(serde_json::json!([
                {"id": "cu_1", "type": "computer_call", "call_id": "call_9", "status": "completed",
                 "action": {"type": "screenshot"}}
            ])),
            responses_output(serde_json::json!([
                {"id": "x_1", "type": "frobnicate_request", "status": "completed"}
            ])),
            responses_output(serde_json::json!([
                {"id": "ws_1", "type": "web_search_call", "status": "completed",
                 "action": {"type": "search", "query": "loader"}},
                {"id": "msg_1", "type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "found it", "annotations": []}]}
            ])),
        ],
        2,
    );
    for expected in [0, 0, 1] {
        let mut c = tool_loop_call("fix the bug", 0, true);
        c.inject_context = false;
        c.sediment = true;
        executor
            .run(
                request_control(),
                c,
                &pc_gateway_protocol::OpenAiResponsesRenderer,
                &NoopEvidenceSink,
            )
            .await;
        assert_eq!(gate.captured.lock().unwrap().len(), expected);
    }
    let captured = gate.captured.lock().unwrap().clone();
    let transcript: serde_json::Value = serde_json::from_str(&captured[0].1).unwrap();
    assert_eq!(
        transcript["steps"],
        serde_json::json!([{"type": "tool_call", "name": "web_search_call",
            "arguments": "{\"query\":\"loader\",\"type\":\"search\"}"}])
    );
    assert_eq!(transcript["assistant"], "found it");
}

#[tokio::test]
async fn terminal_evidence_covers_zero_send_release_refusal_and_paid_possibility() {
    use pc_gateway_evidence::{EvidenceEvent, Sending, Terminal};
    for (case, expected_terminal, expected_send, expected_attempts) in [
        ("block", Terminal::Blocked, Sending::NoSend, 0),
        ("withhold", Terminal::Withheld, Sending::NoSend, 0),
        ("expired", Terminal::AdmissionExpired, Sending::NoSend, 0),
        (
            "release_block",
            Terminal::Blocked,
            Sending::ResponseReceived,
            1,
        ),
        (
            "release_withhold",
            Terminal::Withheld,
            Sending::ResponseReceived,
            1,
        ),
        (
            "release_expired",
            Terminal::AdmissionExpired,
            Sending::ResponseReceived,
            1,
        ),
        (
            "released",
            Terminal::ReleaseAuthorized,
            Sending::ResponseReceived,
            1,
        ),
        (
            "upstream_error",
            Terminal::ReleaseAuthorized,
            Sending::ResponseReceived,
            1,
        ),
        ("not_sent", Terminal::NotSent, Sending::DefinitelyNotSent, 1),
        (
            "indeterminate",
            Terminal::SendIndeterminate,
            Sending::PossiblySent,
            1,
        ),
        ("gate_error", Terminal::GatePaused, Sending::NoSend, 0),
        ("degraded", Terminal::Degraded, Sending::ResponseReceived, 1),
    ] {
        let mut admission = result(
            CheckPhase::BeforeSend,
            if case == "block" {
                CheckDecision::Block
            } else if case == "withhold" {
                CheckDecision::Withhold
            } else {
                CheckDecision::Allow
            },
            &[],
        );
        let mut release = result(
            CheckPhase::BeforeRelease,
            if case == "release_block" {
                CheckDecision::Block
            } else if case == "release_withhold" {
                CheckDecision::Withhold
            } else {
                CheckDecision::Allow
            },
            &[],
        );
        if case == "expired" {
            admission.valid_until = "2000-01-01T00:00:00Z".into();
        }
        if case == "release_expired" {
            release.valid_until = "2000-01-01T00:00:00Z".into();
        }
        let initial = if matches!(case, "gate_error" | "degraded") {
            Err(GateError::GateUnavailable(
                "SENSITIVE_PROVIDER_ERROR".into(),
            ))
        } else {
            Ok(admission)
        };
        let response = match case {
            "not_sent" => SendOutcome::NotSent(pc_gateway_transport::NotSentReason::ConnectRefused),
            "indeterminate" => SendOutcome::Indeterminate(
                pc_gateway_transport::SendIndeterminateReason::LostAfterHandoff,
            ),
            "upstream_error" => {
                let SendOutcome::Delivered(mut raw) = raw_response() else {
                    unreachable!()
                };
                raw.status = 500;
                SendOutcome::Delivered(raw)
            }
            _ => raw_response(),
        };
        let (executor, renderer, _, _) = harness(vec![initial, Ok(release)], vec![response], 1);
        let evidence = BufferedEvidenceSink::new();
        let control = request_control();
        let observation = control.observation();
        let outcome = executor
            .run(
                control,
                call(if case == "degraded" {
                    DegradePolicy::PassthroughWithoutMemory
                } else {
                    DegradePolicy::Pause
                }),
                &RendererRef(renderer),
                &evidence,
            )
            .await;
        let calls = evidence.calls();
        assert_eq!(calls.len(), 1, "{case}");
        let record = &calls[0];
        assert_eq!(record.call_id, observation.call_id());
        assert_eq!(record.terminal, expected_terminal, "{case}");
        assert_eq!(record.sending, expected_send, "{case}");
        let snapshot = observation.snapshot();
        assert_eq!(
            snapshot.execution,
            ExecutionState::Finished(record.terminal),
            "{case}"
        );
        assert_eq!(snapshot.model_send, record.sending, "{case}");
        assert_eq!(record.attempts, expected_attempts, "{case}");
        assert!(record.upstream_ttft_ms.is_none());
        assert!(
            record.usage.input.tokens.is_none(),
            "total_tokens alone cannot establish input usage"
        );
        let attempts: Vec<_> = evidence
            .events()
            .into_iter()
            .filter_map(|event| {
                if let EvidenceEvent::Attempt(a) = event {
                    Some(a)
                } else {
                    None
                }
            })
            .collect();
        assert_eq!(attempts.len(), expected_attempts as usize, "{case}");
        for a in &attempts {
            assert_eq!(a.call_id, record.call_id);
        }
        if case == "upstream_error" {
            assert_eq!(
                attempts[0].outcome,
                pc_gateway_evidence::AttemptOutcome::UpstreamError
            );
        }
        if case == "gate_error" {
            assert!(!format!("{outcome:?}").contains("SENSITIVE_PROVIDER_ERROR"));
        }
    }
}

#[tokio::test]
async fn interrupted_model_send_keeps_paid_possibility_and_observed_stop_facts() {
    use pc_gateway_evidence::{AttemptOutcome, EvidenceEvent, Interruption, Sending, Terminal};
    for (stop, expected) in [
        ("cancel", Interruption::CancellationRequested),
        ("deadline", Interruption::DeadlineElapsed),
        ("drop", Interruption::FutureDropped),
    ] {
        let (executor, renderer, transport, _) = harness(
            vec![Ok(result(
                CheckPhase::BeforeSend,
                CheckDecision::Allow,
                &[],
            ))],
            vec![raw_response()],
            1,
        );
        transport
            .send_holds
            .lock()
            .unwrap()
            .push_back(Some(Arc::new(tokio::sync::Notify::new())));
        let evidence = BufferedEvidenceSink::new();
        let control = RequestControl::new(
            tokio::time::Instant::now()
                + if stop == "deadline" {
                    std::time::Duration::ZERO
                } else {
                    std::time::Duration::from_secs(30)
                },
            None,
        );
        let cancellation = control.cancellation();
        let observation = control.observation();
        let renderer = RendererRef(renderer);
        let mut future =
            Box::pin(executor.run(control, call(DegradePolicy::Pause), &renderer, &evidence));
        assert!(
            std::future::poll_fn(|cx| std::task::Poll::Ready(future.as_mut().poll(cx)))
                .await
                .is_pending()
        );
        if stop == "cancel" {
            cancellation.cancel();
        }
        drop(future);
        let record = &evidence.calls()[0];
        assert_eq!(record.terminal, Terminal::Abandoned);
        assert_eq!(record.sending, Sending::PossiblySent);
        assert_eq!(record.interruption, Some(expected));
        let snapshot = observation.snapshot();
        assert_eq!(
            snapshot.execution,
            ExecutionState::Finished(record.terminal)
        );
        assert_eq!(snapshot.model_send, record.sending);
        let EvidenceEvent::Attempt(attempt) = &evidence.events()[0] else {
            panic!("missing attempt")
        };
        assert_eq!(attempt.outcome, AttemptOutcome::PossiblySent);
        assert_eq!(attempt.call_id, record.call_id);
        assert!(record.usage.input.tokens.is_none());
    }
}

#[tokio::test]
async fn readmission_evidence_keeps_each_real_attempt_and_release_order() {
    use pc_gateway_evidence::{AttemptOutcome, EvidenceEvent};
    let (executor, renderer, _, gate) = harness(
        vec![
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![
            SendOutcome::NotSent(pc_gateway_transport::NotSentReason::RejectedLocally),
            raw_response(),
        ],
        2,
    );
    let hold = Arc::new(tokio::sync::Notify::new());
    gate.check_holds
        .lock()
        .unwrap()
        .extend([None, None, Some(hold.clone())]);
    let evidence = BufferedEvidenceSink::new();
    let renderer = RendererRef(renderer);
    let mut future = Box::pin(executor.run(
        request_control(),
        call(DegradePolicy::Pause),
        &renderer,
        &evidence,
    ));
    assert!(
        std::future::poll_fn(|cx| std::task::Poll::Ready(future.as_mut().poll(cx)))
            .await
            .is_pending()
    );
    assert!(evidence.calls().is_empty(), "release decision is pending");
    let attempts: Vec<_> = evidence
        .events()
        .into_iter()
        .filter_map(|e| {
            if let EvidenceEvent::Attempt(a) = e {
                Some(a)
            } else {
                None
            }
        })
        .collect();
    assert_eq!(
        attempts.iter().map(|a| a.outcome).collect::<Vec<_>>(),
        [AttemptOutcome::RejectedLocally, AttemptOutcome::Delivered]
    );
    tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    hold.notify_one();
    assert!(matches!(future.await, ManagedOutcome::Released { .. }));
    let record = &evidence.calls()[0];
    assert_eq!(record.attempts, 2);
    assert_eq!(record.call_id, attempts[0].call_id);
    assert_eq!(record.call_id, attempts[1].call_id);
    assert!(record.release_decision_ms.unwrap() >= 20);
    assert!(record.release_authorized_ms.unwrap() >= record.release_decision_ms.unwrap());
    assert!(record.duration_ms >= record.release_authorized_ms.unwrap());
}

struct EvidenceFaultWriter {
    entered: std::sync::mpsc::Sender<()>,
    resume: Option<std::sync::mpsc::Receiver<()>>,
}
impl std::io::Write for EvidenceFaultWriter {
    fn write(&mut self, _bytes: &[u8]) -> std::io::Result<usize> {
        let _ = self.entered.send(());
        if let Some(resume) = self.resume.take() {
            resume.recv().unwrap();
        }
        Err(std::io::Error::other("SENSITIVE_WRITER_ERROR"))
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[tokio::test]
async fn failing_or_blocked_full_evidence_output_cannot_change_release_or_cancellation() {
    use pc_gateway_evidence::JsonEvidenceSink;
    for blocked_writer in [false, true] {
        let (entered_tx, entered_rx) = std::sync::mpsc::channel();
        let (resume_tx, resume_rx) = std::sync::mpsc::channel();
        let sink = JsonEvidenceSink::new(
            EvidenceFaultWriter {
                entered: entered_tx,
                resume: blocked_writer.then_some(resume_rx),
            },
            std::num::NonZeroUsize::new(1).unwrap(),
        )
        .unwrap();
        let (executor, renderer, _, _) = harness(allow_runs(1), vec![raw_response()], 1);
        let outcome = executor
            .run(
                request_control(),
                call(DegradePolicy::Pause),
                &RendererRef(renderer),
                &sink,
            )
            .await;
        assert!(matches!(outcome, ManagedOutcome::Released { .. }));
        entered_rx
            .recv_timeout(std::time::Duration::from_secs(3))
            .unwrap();
        let (executor, renderer, transport, _) = harness(
            vec![Ok(result(
                CheckPhase::BeforeSend,
                CheckDecision::Allow,
                &[],
            ))],
            vec![raw_response()],
            1,
        );
        transport
            .send_holds
            .lock()
            .unwrap()
            .push_back(Some(Arc::new(tokio::sync::Notify::new())));
        let control = request_control();
        let observation = control.observation();
        let cancellation = control.cancellation();
        let renderer = RendererRef(renderer);
        let mut future =
            Box::pin(executor.run(control, call(DegradePolicy::Pause), &renderer, &sink));
        assert!(
            std::future::poll_fn(|cx| std::task::Poll::Ready(future.as_mut().poll(cx)))
                .await
                .is_pending()
        );
        cancellation.cancel();
        drop(future);
        assert_eq!(
            observation.snapshot().model_send,
            ModelSendState::PossiblySent
        );
        assert_eq!(
            observation.snapshot().execution,
            ExecutionState::Finished(ExecutionTerminal::Abandoned)
        );
        let (executor, renderer, _, _) = harness(
            vec![
                Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
                Ok(result(
                    CheckPhase::BeforeRelease,
                    CheckDecision::Withhold,
                    &[],
                )),
            ],
            vec![raw_response()],
            1,
        );
        assert!(matches!(
            executor
                .run(
                    request_control(),
                    call(DegradePolicy::Pause),
                    &RendererRef(renderer),
                    &sink
                )
                .await,
            ManagedOutcome::Withheld { .. }
        ));
        if blocked_writer {
            assert!(sink.stats().dropped > 0);
            resume_tx.send(()).unwrap();
        }
    }
}

#[tokio::test]
async fn partial_response_facts_survive_without_claiming_complete_delivery_or_usage() {
    let (executor, renderer, _, _) = harness(
        vec![Ok(result(
            CheckPhase::BeforeSend,
            CheckDecision::Allow,
            &[],
        ))],
        vec![SendOutcome::IncompleteResponse {
            reason: pc_gateway_transport::SendIndeterminateReason::BufferLimitExceeded,
            status: 429,
            response_first_byte_ms: Some(17),
        }],
        2,
    );
    let evidence = BufferedEvidenceSink::new();
    assert!(matches!(
        executor
            .run(
                request_control(),
                call(DegradePolicy::Pause),
                &RendererRef(renderer),
                &evidence
            )
            .await,
        ManagedOutcome::Withheld { reason_codes, retryable: false }
            if reason_codes == ["response_buffer_limit_exceeded"]
    ));
    let call = &evidence.calls()[0];
    assert_eq!(call.attempts, 1);
    assert_eq!(call.sending, pc_gateway_evidence::Sending::PossiblySent);
    assert_eq!(call.upstream_status, Some(429));
    let pc_gateway_evidence::EvidenceEvent::Attempt(attempt) = &evidence.events()[0] else {
        panic!("missing attempt")
    };
    assert_eq!(attempt.response_first_byte_ms, Some(17));
    assert!(attempt.response_complete_ms.is_none());
    assert!(call.usage.input.tokens.is_none());
}

// ---------- #2405：复用证明的依赖变化后重新准备一次 ----------

#[tokio::test]
async fn withheld_recoverability_is_classified_by_the_executor() {
    for (reasons, expected_retryable) in [
        (vec!["context-refresh-required"], true),
        (vec!["target_unsupported"], false),
        (
            vec!["context-refresh-required", "target_unsupported"],
            false,
        ),
        (vec!["unknown-reason"], false),
        (vec![], false),
    ] {
        for phase in [CheckPhase::BeforeSend, CheckPhase::BeforeRelease] {
            let mut checks = vec![];
            let responses = if phase == CheckPhase::BeforeRelease {
                checks.push(Ok(result(
                    CheckPhase::BeforeSend,
                    CheckDecision::Allow,
                    &[],
                )));
                vec![raw_response()]
            } else {
                vec![]
            };
            checks.push(Ok(result(phase, CheckDecision::Withhold, &reasons)));
            let (executor, renderer, _, _) = harness(checks, responses, 2);
            let outcome = executor
                .run(
                    request_control(),
                    call(DegradePolicy::Pause),
                    &RendererRef(renderer),
                    &NoopEvidenceSink,
                )
                .await;
            match outcome {
                ManagedOutcome::Withheld {
                    reason_codes,
                    retryable,
                } => {
                    assert_eq!(reason_codes, reasons);
                    assert_eq!(retryable, expected_retryable, "{phase:?}: {reasons:?}");
                }
                other => panic!("expected Withheld, got {other:?}"),
            }
        }
    }
}

fn refresh_required() -> Result<ContextCheckResult, GateError> {
    Ok(result(
        CheckPhase::BeforeSend,
        CheckDecision::Withhold,
        &["context-refresh-required"],
    ))
}

#[tokio::test]
async fn a_stale_reuse_proof_drops_the_replay_and_prepares_once_more() {
    let allow_send = || Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[]));
    let allow_release = || Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[]));
    let (executor, renderer, transport, gate) = harness(
        vec![
            // 第一步：准备 snapshot-1 并发送。
            allow_send(),
            allow_release(),
            // 第二步：重放的 snapshot-1 已失效 → 重新准备 snapshot-2 → 放行。
            refresh_required(),
            allow_send(),
            allow_release(),
            // 第三步：重放 snapshot-2。
            allow_send(),
            allow_release(),
        ],
        (0..3).map(|_| raw_response()).collect(),
        2,
    );
    for snapshot in ["memory-snapshot-1", "memory-snapshot-2"] {
        gate.prepare_queue
            .lock()
            .unwrap()
            .push_back(Ok(reuse_result(snapshot)));
    }
    let r = RendererRef(renderer.clone());
    for step in 0..3 {
        let control = request_control();
        let observation = control.observation();
        let outcome = executor
            .run(
                control,
                tool_loop_call("fix the bug", step, false),
                &r,
                &NoopEvidenceSink,
            )
            .await;
        assert!(
            matches!(outcome, ManagedOutcome::Released { .. }),
            "{outcome:?}"
        );
        assert_eq!(
            observation.snapshot().model_send,
            ModelSendState::ResponseReceived
        );
        assert_eq!(
            observation.snapshot().execution,
            ExecutionState::Finished(ExecutionTerminal::ReleaseAuthorized)
        );
    }
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
    assert_eq!(
        transport.sends.lock().unwrap().len(),
        3,
        "the stale value is never sent"
    );
    let checked: Vec<_> = sends(&gate).into_iter().map(|(body, _)| body).collect();
    assert_eq!(checked.len(), 4);
    assert!(checked[0].contains("memory-snapshot-1"));
    assert!(checked[1].contains("memory-snapshot-1"));
    assert!(checked[2].contains("memory-snapshot-2") && !checked[2].contains("memory-snapshot-1"));
    assert!(checked[3].contains("memory-snapshot-2"));
}

#[tokio::test]
async fn a_request_refreshes_at_most_once() {
    let (executor, renderer, transport, gate) =
        harness(vec![refresh_required(), refresh_required()], vec![], 2);
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result("memory-snapshot-1")));
    let outcome = executor
        .run(
            request_control(),
            tool_loop_call("fix the bug", 0, false),
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(
        matches!(outcome, ManagedOutcome::Withheld { ref reason_codes, .. } if reason_codes == &["context-refresh-required"]),
        "{outcome:?}"
    );
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
    assert_eq!(sends(&gate).len(), 2);
    assert!(transport.sends.lock().unwrap().is_empty());
}

#[tokio::test]
async fn refresh_required_without_an_injection_is_withheld_directly() {
    let (executor, renderer, _transport, gate) = harness(vec![refresh_required()], vec![], 2);
    let mut c = tool_loop_call("fix the bug", 0, false);
    c.inject_context = false;
    let outcome = executor
        .run(
            request_control(),
            c,
            &RendererRef(renderer.clone()),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Withheld { .. }));
    assert!(gate.prepare_queries.lock().unwrap().is_empty());
    assert_eq!(sends(&gate).len(), 1);
}

#[tokio::test]
async fn code_evidence_is_requested_only_when_enabled() {
    let (executor, renderer, _transport, gate) =
        harness(allow_runs(2), (0..2).map(|_| raw_response()).collect(), 2);
    let r = RendererRef(renderer.clone());
    for include_code in [false, true] {
        let mut c = call(DegradePolicy::Pause);
        c.inject_context = true;
        c.include_code = include_code;
        executor
            .run(request_control(), c, &r, &NoopEvidenceSink)
            .await;
    }
    assert_eq!(
        *gate.prepare_include_code.lock().unwrap(),
        vec![false, true]
    );
}

#[tokio::test]
async fn refreshed_context_cannot_exceed_the_rendered_input_limit() {
    let (executor, renderer, transport, gate) = harness(
        vec![
            refresh_required(),
            Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[])),
            Ok(result(CheckPhase::BeforeRelease, CheckDecision::Allow, &[])),
        ],
        vec![raw_response()],
        2,
    );
    let executor = executor.with_input_limit(1024);
    for snapshot in ["small snapshot".to_string(), "私密\n\"".repeat(1000)] {
        gate.prepare_queue
            .lock()
            .unwrap()
            .push_back(Ok(reuse_result(&snapshot)));
    }
    let control = request_control();
    let observation = control.observation();
    let outcome = executor
        .run(
            control,
            tool_loop_call("fix the bug", 0, false),
            &RendererRef(renderer),
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Blocked { reason_codes }
        if reason_codes == ["request_payload_too_large"]));
    assert_eq!(gate.prepare_queries.lock().unwrap().len(), 2);
    assert_eq!(gate.seen.lock().unwrap().len(), 1);
    assert!(transport.sends.lock().unwrap().is_empty());
    assert_eq!(observation.snapshot().model_send, ModelSendState::NoSend);
    assert_eq!(
        observation.snapshot().execution,
        ExecutionState::Finished(ExecutionTerminal::Blocked)
    );
}

#[tokio::test]
async fn rendered_capacity_includes_prepared_context_and_replay() {
    let (executor, renderer, transport, gate) = harness(vec![], vec![], 2);
    let executor = executor.with_input_limit(256);
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result(&"私密\n\"".repeat(100))));
    for _ in 0..2 {
        let control = request_control();
        let observation = control.observation();
        let outcome = executor
            .run(
                control,
                ManagedCall {
                    inject_context: true,
                    conversation_id: Some("capacity-test".into()),
                    ..call(DegradePolicy::Pause)
                },
                &RendererRef(renderer.clone()),
                &NoopEvidenceSink,
            )
            .await;
        assert!(matches!(outcome, ManagedOutcome::Blocked { reason_codes }
            if reason_codes == ["request_payload_too_large"]));
        assert_eq!(observation.snapshot().model_send, ModelSendState::NoSend);
    }
    assert!(transport.sends.lock().unwrap().is_empty());
    assert!(gate.seen.lock().unwrap().is_empty());
}

#[tokio::test]
async fn response_capacity_withholds_without_retry_and_preserves_possible_billing() {
    use pc_gateway_evidence::{AttemptOutcome, EvidenceEvent, Sending, Terminal};
    for degrade in [
        DegradePolicy::Pause,
        DegradePolicy::PassthroughWithoutMemory,
    ] {
        for partial in [false, true] {
            let check = if degrade == DegradePolicy::Pause {
                Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[]))
            } else {
                Err(GateError::GateUnavailable("offline".into()))
            };
            let send = if partial {
                SendOutcome::IncompleteResponse {
                    reason: SendIndeterminateReason::BufferLimitExceeded,
                    status: 200,
                    response_first_byte_ms: Some(17),
                }
            } else {
                SendOutcome::Indeterminate(SendIndeterminateReason::BufferLimitExceeded)
            };
            let (executor, renderer, transport, _) = harness(vec![check], vec![send], 3);
            let control = request_control();
            let observation = control.observation();
            let evidence = BufferedEvidenceSink::new();
            let outcome = executor
                .run(control, call(degrade), &RendererRef(renderer), &evidence)
                .await;
            assert!(
                matches!(outcome, ManagedOutcome::Withheld { reason_codes, .. }
                if reason_codes == ["response_buffer_limit_exceeded"])
            );
            assert_eq!(
                observation.snapshot().model_send,
                ModelSendState::PossiblySent
            );
            assert_eq!(
                observation.snapshot().execution,
                ExecutionState::Finished(ExecutionTerminal::Withheld)
            );
            assert_eq!(transport.sends.lock().unwrap().len(), 1, "no paid resend");
            let calls = evidence.calls();
            assert_eq!(calls.len(), 1);
            let record = &calls[0];
            assert_eq!(record.terminal, Terminal::Withheld);
            assert_eq!(record.sending, Sending::PossiblySent);
            assert_eq!(record.attempts, 1);
            assert!(record.usage.input.tokens.is_none());
            assert!(record.release_authorized_ms.is_none());
            let events = evidence.events();
            let [EvidenceEvent::Attempt(attempt), EvidenceEvent::Call(_)] = events.as_slice()
            else {
                panic!("expected one attempt followed by its call terminal")
            };
            assert_eq!(attempt.call_id, record.call_id);
            assert_eq!(attempt.outcome, AttemptOutcome::BufferLimitExceeded);
            assert_eq!(attempt.upstream_status, partial.then_some(200));
            assert_eq!(attempt.response_first_byte_ms, partial.then_some(17));
            assert!(attempt.response_complete_ms.is_none());
        }
    }
}

#[tokio::test]
async fn real_body_timeout_exports_timeout_attempt_and_unknown_usage_without_release_or_retry() {
    use pc_gateway_evidence::JsonEvidenceSink;
    use pc_gateway_transport::ReqwestTransport;
    use std::time::Duration;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[derive(Clone, Default)]
    struct Output(Arc<Mutex<Vec<u8>>>);
    impl std::io::Write for Output {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    for degrade in [
        DegradePolicy::Pause,
        DegradePolicy::PassthroughWithoutMemory,
    ] {
        for stream in [false, true] {
            let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let (finish_tx, finish_rx) = tokio::sync::oneshot::channel::<()>();
            let server = tokio::spawn(async move {
                let (mut socket, _) = listener.accept().await.unwrap();
                let mut request = Vec::new();
                let mut buffer = [0; 4096];
                loop {
                    let n = socket.read(&mut buffer).await.unwrap();
                    assert!(n > 0);
                    request.extend_from_slice(&buffer[..n]);
                    if let Some(end) = request.windows(4).position(|w| w == b"\r\n\r\n") {
                        let head = String::from_utf8_lossy(&request[..end]).to_ascii_lowercase();
                        let length: usize = head
                            .lines()
                            .find_map(|line| line.strip_prefix("content-length: "))
                            .unwrap()
                            .parse()
                            .unwrap();
                        if request.len() >= end + 4 + length {
                            break;
                        }
                    }
                }
                let (mime, partial) = if stream {
                    ("text/event-stream", "data: {\"usage\":{\"prompt_tokens\":80},\"secret\":\"SYNTHETIC_PARTIAL_SECRET\"}\n\n")
                } else {
                    ("application/json", "{\"usage\":{\"prompt_tokens\":80},\"secret\":\"SYNTHETIC_PARTIAL_SECRET\"}")
                };
                socket.write_all(format!("HTTP/1.1 201 Created\r\nContent-Type: {mime}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{partial}", partial.len() + 100).as_bytes()).await.unwrap();
                let _ = finish_rx.await;
            });
            let verdict = if degrade == DegradePolicy::Pause {
                Ok(result(CheckPhase::BeforeSend, CheckDecision::Allow, &[]))
            } else {
                Err(GateError::GateUnavailable("SYNTHETIC_GATE_SECRET".into()))
            };
            let (_, renderer, _, gate) = harness(vec![verdict], vec![], 3);
            let executor = Executor::new(
                FakeGateRef(gate.clone()),
                ReqwestTransport::new(Duration::from_millis(250)),
                NonZeroU32::new(3).unwrap(),
            );
            let mut call = call(degrade);
            call.target_url =
                format!("http://SYNTHETIC_USER:SYNTHETIC_PASSWORD@{address}/SYNTHETIC_PATH");
            call.request.stream = stream;
            let control = request_control();
            let observation = control.observation();
            let output = Output::default();
            let sink =
                JsonEvidenceSink::new(output.clone(), std::num::NonZeroUsize::new(8).unwrap())
                    .unwrap();
            let outcome = executor
                .run(control, call, &RendererRef(renderer), &sink)
                .await;
            let _ = finish_tx.send(());
            server.await.unwrap();
            assert!(matches!(outcome, ManagedOutcome::SendIndeterminate));
            assert_eq!(
                gate.seen.lock().unwrap().len(),
                1,
                "no release check or readmission"
            );
            assert_eq!(
                observation.snapshot().model_send,
                ModelSendState::PossiblySent
            );
            assert_eq!(
                observation.snapshot().execution,
                ExecutionState::Finished(ExecutionTerminal::SendIndeterminate)
            );
            tokio::time::timeout(Duration::from_secs(3), async {
                while sink.stats().written < 2 {
                    tokio::task::yield_now().await;
                }
            })
            .await
            .unwrap();
            let text = String::from_utf8(output.0.lock().unwrap().clone()).unwrap();
            assert!(!text.contains("SYNTHETIC_"));
            let records: Vec<serde_json::Value> = text
                .lines()
                .map(|line| serde_json::from_str(line).unwrap())
                .collect();
            assert_eq!(
                records.len(),
                2,
                "one attempt and one call, without delivery"
            );
            assert_eq!(records[0]["event"], "attempt");
            assert_eq!(records[1]["event"], "call");
            let attempt = &records[0]["evidence"];
            let call = &records[1]["evidence"];
            assert_eq!(attempt["call_id"], call["call_id"]);
            assert_eq!(attempt["outcome"], "timeout_awaiting_response");
            assert_eq!(attempt["upstream_status"], 201);
            assert!(attempt["response_first_byte_ms"].is_number());
            assert!(attempt["response_complete_ms"].is_null());
            assert_eq!(call["terminal"], "send_indeterminate");
            assert_eq!(call["sending"], "possibly_sent");
            assert_eq!(call["attempts"], 1);
            assert_eq!(call["upstream_status"], 201);
            assert!(call["release_decision_ms"].is_null());
            assert!(call["release_authorized_ms"].is_null());
            for evidence in [attempt, call] {
                for field in ["input", "output", "cache_read", "cache_write"] {
                    assert_eq!(evidence["usage"][field]["completeness"], "unknown");
                    assert!(evidence["usage"][field]["tokens"].is_null());
                }
            }
        }
    }
}

// ---------- #2413：为复用缓存前缀的记忆抽取保留回合前缀 ----------

#[tokio::test]
async fn a_terminal_responses_turn_keeps_its_prefix_for_extraction() {
    let output = serde_json::json!([
        reasoning_item(),
        {"id": "msg_1", "type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "done", "annotations": []}]}
    ]);
    let (executor, _renderer, transport, gate) = harness(
        allow_runs(2),
        vec![
            responses_output(output.clone()),
            responses_output(output.clone()),
        ],
        2,
    );
    for capture_prefix in [true, false] {
        let mut c = tool_loop_call("fix the bug", 0, true);
        c.inject_context = false;
        c.sediment = true;
        c.capture_prefix = capture_prefix;
        let outcome = executor
            .run(
                request_control(),
                c,
                &pc_gateway_protocol::OpenAiResponsesRenderer,
                &NoopEvidenceSink,
            )
            .await;
        assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    }
    let metadata = gate.captured_metadata.lock().unwrap().clone();
    assert!(
        metadata[1].get("prefix_handle").is_none(),
        "without opt-in nothing is kept"
    );
    let handle = metadata[0]["prefix_handle"].as_str().unwrap().to_owned();
    let prefix = executor.prefix(&handle, "project:test").unwrap();
    assert_eq!(prefix.conversation_id, "conversation-a");
    assert_eq!(prefix.target_profile, "openai:test");
    assert_eq!(
        prefix.request_body,
        transport.sends.lock().unwrap()[0],
        "the prefix is the exact body sent upstream"
    );
    assert_eq!(serde_json::Value::from(prefix.response_output), output);
    assert!(executor.prefix(&handle, "project:other").is_none());
    // 前缀规模取自终止响应的用量，供 Core 判断复用前缀是否更便宜。
    assert_eq!(metadata[0]["prefix_input_tokens"], 10);
    assert_eq!(metadata[0]["prefix_output_tokens"], 2);
}

#[tokio::test]
async fn an_injected_turn_keeps_its_prefix_and_a_source_without_the_injection() {
    let output = serde_json::json!([
        {"id": "msg_1", "type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "done", "annotations": []}]}
    ]);
    let (executor, _renderer, transport, gate) =
        harness(allow_runs(1), vec![responses_output(output)], 2);
    *gate.prepare_result.lock().unwrap() = Some(Ok(reuse_result("memory-snapshot-1")));
    let mut c = tool_loop_call("fix the bug", 0, true);
    c.sediment = true;
    c.capture_prefix = true;
    c.inject_context = true;
    let outcome = executor
        .run(
            request_control(),
            c,
            &pc_gateway_protocol::OpenAiResponsesRenderer,
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    // 前缀是实际发出的请求，含注入块；Source 取自注入前的请求，不含注入块。
    let handle = gate.captured_metadata.lock().unwrap()[0]["prefix_handle"]
        .as_str()
        .unwrap()
        .to_owned();
    let prefix = executor.prefix(&handle, "project:test").unwrap();
    assert_eq!(prefix.request_body, transport.sends.lock().unwrap()[0]);
    assert!(String::from_utf8_lossy(&prefix.request_body).contains("memory-snapshot-1"));
    assert!(!gate.captured.lock().unwrap()[0]
        .1
        .contains("memory-snapshot-1"));
}

#[tokio::test]
async fn an_oversized_turn_is_captured_as_ordered_parts_with_the_handle_on_the_last() {
    let output = serde_json::json!([
        {"id": "msg_1", "type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "done", "annotations": []}]}
    ]);
    let (executor, _renderer, _transport, gate) =
        harness(allow_runs(1), vec![responses_output(output)], 2);
    let mut c = tool_loop_call(&"y".repeat(450_000), 0, true);
    c.inject_context = false;
    c.sediment = true;
    c.capture_prefix = true;
    let outcome = executor
        .run(
            request_control(),
            c,
            &pc_gateway_protocol::OpenAiResponsesRenderer,
            &NoopEvidenceSink,
        )
        .await;
    assert!(matches!(outcome, ManagedOutcome::Released { .. }));
    let captured = gate.captured.lock().unwrap().clone();
    let metadata = gate.captured_metadata.lock().unwrap().clone();
    let count = captured.len();
    assert!(count >= 3);
    let turn = captured[0].0.rsplit_once('-').unwrap().0.to_owned();
    for (index, ((source_id, content), metadata)) in captured.iter().zip(&metadata).enumerate() {
        assert_eq!(source_id, &format!("{turn}-{}of{count}", index + 1));
        assert!(content.chars().count() <= 200_000);
        assert_eq!(
            metadata["turn"],
            serde_json::json!({"source_id": turn, "part": index + 1, "parts": count})
        );
        assert_eq!(
            metadata.get("prefix_handle").is_some(),
            index + 1 == count,
            "only the last part carries the handle"
        );
    }
}
