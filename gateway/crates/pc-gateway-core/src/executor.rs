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

//! 执行器核心：一次托管调用的状态机。
//!
//! ```text
//! 宿主完整输入 ─► [快照原始请求] ─► enrich ─► 渲染 ─► check(before_send)
//!                   │                                       │
//!                   │                    ┌── allow + binding + valid_until
//!                   │                    ▼
//!                   │            [验有效期] ─► 发送 ─► ┌─ Delivered ─► check(before_release)
//!                   │                    │             ├─ NotSent ─► [重准入] ─► 重新发送
//!                   │                    │             └─ Indeterminate ─► 上抛
//!                   │                    │
//!                   │              到期→ AdmissionExpired（零发送）
//!                   │
//!                   └── 降级时用此原始请求（不含注入）重新渲染
//! ```

use crate::evidence::CallRecorder;
use crate::lifecycle::Execution;
use crate::prefix_store::{PrefixEntry, PrefixStore};
use crate::replay_cache::{message_anchor, PreparedBlock, ReplayCache, MAX_REPLAY_MS};
use crate::transcript::turn_transcript;

/// Core 的 before_send 在复用证明的依赖已变化或证明已失效时给出的 withhold 原因。
const CONTEXT_REFRESH_REQUIRED: &str = "context-refresh-required";
use crate::{GateClient, GateError};
use crate::{ModelSendState, RequestControl};
use pc_gateway_evidence::EvidenceSink;
use pc_gateway_evidence::UsageMapping;
use pc_gateway_protocol::{CanonicalRequest, WireRenderer};
use pc_gateway_transport::{AdmittedSend, SendIndeterminateReason, SendOutcome, Transport};
use pc_gateway_types::{
    parse_rfc3339_ms, CaptureContentSourceRequest, CheckDecision, CheckPhase, ContextCheckRequest,
    ContextCheckResult, ModelJsonPayload, ModelJsonTextLocation, PrepareContextRequest,
    PreparedContextLocation,
};
use std::collections::BTreeMap;
use std::num::NonZeroU32;
use std::time::{SystemTime, UNIX_EPOCH};

/// Gate 不可用时的策略（PR #2199 故障语义）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DegradePolicy {
    #[default]
    Pause,
    PassthroughWithoutMemory,
}

/// 宿主交给执行器的完整输入。
pub struct ManagedCall {
    pub scope_id: String,
    pub target_profile: String,
    pub request: CanonicalRequest,
    pub target_url: String,
    pub headers: Vec<(String, String)>,
    pub degrade_policy: DegradePolicy,
    pub inject_context: bool,
    /// 注入时同时请求代码证据（默认关）。
    pub include_code: bool,
    /// 宿主提供的会话 ID（`X-PowerContext-Conversation-ID`）。只有带它的请求才在会话内重放准备结果。
    pub conversation_id: Option<String>,
    /// 沉淀时保留本回合的上游请求与响应输出，供 Core 复用前缀缓存抽取记忆（#2413，默认关）。
    pub capture_prefix: bool,
    pub sediment: bool,
    pub prepared_contexts: Vec<PreparedContextLocation>,
}

/// 执行结果。
#[derive(Debug)]
pub enum ManagedOutcome {
    Released {
        binding: Option<String>,
        status: u16,
        content_type: String,
        body: bytes::Bytes,
    },
    Blocked {
        reason_codes: Vec<String>,
    },
    Withheld {
        reason_codes: Vec<String>,
        /// Whether refreshing context can recover this outcome.
        retryable: bool,
    },
    SendIndeterminate,
    NotSent {
        attempts: u32,
    },
    GatePaused(String),
    Degraded {
        status: u16,
        content_type: String,
        body: bytes::Bytes,
    },
    /// 准入已过期（valid_until 超窗）：零发送或零释放，可安全重走完整流程。
    AdmissionExpired,
}

impl ManagedOutcome {
    // Keep recovery classification beside the executor's context refresh handling.
    fn withheld(reason_codes: Vec<String>) -> Self {
        let retryable = !reason_codes.is_empty()
            && reason_codes
                .iter()
                .all(|reason| reason == CONTEXT_REFRESH_REQUIRED);
        Self::Withheld {
            reason_codes,
            retryable,
        }
    }

    /// One terminal interpretation shared by lifecycle snapshots and evidence.
    pub(crate) fn terminal(&self) -> crate::ExecutionTerminal {
        use crate::ExecutionTerminal;
        match self {
            Self::Released { .. } => ExecutionTerminal::ReleaseAuthorized,
            Self::Blocked { .. } => ExecutionTerminal::Blocked,
            Self::Withheld { .. } => ExecutionTerminal::Withheld,
            Self::SendIndeterminate => ExecutionTerminal::SendIndeterminate,
            Self::NotSent { .. } => ExecutionTerminal::NotSent,
            Self::GatePaused(_) => ExecutionTerminal::GatePaused,
            Self::Degraded { .. } => ExecutionTerminal::Degraded,
            Self::AdmissionExpired => ExecutionTerminal::AdmissionExpired,
        }
    }
}

pub struct Executor<G, T> {
    gate: G,
    transport: T,
    max_attempts: NonZeroU32,
    /// 会话内按消息锚点重放的准备结果（RFC 2199）。
    replays: ReplayCache,
    max_input_bytes: usize,
    require_privacy_coverage: bool,
    prefixes: PrefixStore,
}

/// 单次发送尝试的准入凭证。
struct Admission {
    binding: String,
    /// 截止时间（Unix 毫秒）。解析失败 = 0 = 立即过期（fail-closed，
    /// 不能因无法解析就当作没有截止时间照常发送）。
    valid_until_ms: u64,
}

impl<G, T> Executor<G, T>
where
    G: GateClient,
    T: Transport,
{
    pub fn new(gate: G, transport: T, max_attempts: NonZeroU32) -> Self {
        Self {
            gate,
            transport,
            max_attempts,
            replays: ReplayCache::default(),
            max_input_bytes: pc_gateway_types::CONTEXT_CHECK_BODY_LIMIT,
            require_privacy_coverage: true,
            prefixes: PrefixStore::default(),
        }
    }

    /// Apply the deployment/Core input intersection. Increasing configuration
    /// cannot bypass the currently supported complete-body parser ceiling.
    pub fn with_input_limit(mut self, limit: usize) -> Self {
        self.max_input_bytes = limit.min(pc_gateway_types::CONTEXT_CHECK_BODY_LIMIT);
        self
    }

    /// Apply enforcement discovered from the trusted Core. Non-enforcing
    /// admission still requires Core's allow verdict, binding, and deadline.
    pub fn with_core_enforcement(mut self, enforcing: bool) -> Self {
        self.require_privacy_coverage = enforcing;
        self
    }

    pub async fn run<R: WireRenderer>(
        &self,
        control: RequestControl,
        call: ManagedCall,
        renderer: &R,
        evidence: &dyn EvidenceSink,
    ) -> ManagedOutcome {
        let mut recording = CallRecorder::new(
            evidence,
            control.observation(),
            control.cancellation(),
            &call.target_url,
            match renderer.message_list_key() {
                "input" => Some(UsageMapping::OpenAiResponses),
                "messages" => Some(UsageMapping::OpenAiChat),
                _ => None,
            },
        );
        let mut execution = control.start();
        let outcome = self
            .execute(call, renderer, evidence, &mut execution, &mut recording)
            .await;
        recording.finish(&outcome);
        execution.finish(&outcome);
        outcome
    }

    async fn execute<R: WireRenderer>(
        &self,
        call: ManagedCall,
        renderer: &R,
        evidence: &dyn EvidenceSink,
        execution: &mut Execution,
        recording: &mut CallRecorder<'_>,
    ) -> ManagedOutcome {
        let mut call = call;
        // P1-3：enrich 之前快照原始请求。降级路径必须用这份（不含注入内容）
        // 重新渲染，否则 prepare 召回的私密记忆会在未经任何检查的情况下外发。
        let pristine_request = call.request.clone();

        let anchor = call
            .inject_context
            .then(|| message_anchor(&pristine_request))
            .flatten();
        let mut refreshed = false;
        let (payload, admission) = loop {
            if call.inject_context {
                self.enrich(&mut call, anchor, renderer.message_list_key(), evidence)
                    .await;
            }

            let payload = match renderer.render(&call.request, None) {
                Ok(p) => p,
                Err(e) => {
                    return ManagedOutcome::Blocked {
                        reason_codes: vec![format!("render_failed:{e}")],
                    }
                }
            };
            // Measure every rendered body, including a refreshed prepare result.
            if payload.bytes.len() > self.max_input_bytes {
                return ManagedOutcome::Blocked {
                    reason_codes: vec!["request_payload_too_large".into()],
                };
            }
            let payload_body = match String::from_utf8(payload.bytes.clone().to_vec()) {
                Ok(text) => text,
                Err(_) => {
                    return ManagedOutcome::Blocked {
                        reason_codes: vec!["invalid_payload_encoding".into()],
                    }
                }
            };
            let model_payload = ModelJsonPayload::new(
                content_headers(&call.headers, payload.content_type),
                payload_body,
            );
            let check = ContextCheckRequest::BeforeSend {
                scope_id: call.scope_id.clone(),
                target_profile: call.target_profile.clone(),
                payload: model_payload,
                prepared_contexts: call.prepared_contexts.clone(),
            };

            let admission = match self.gate.check(check).await {
                Err(e) => {
                    // 降级仅适用于 Core「不可用」；Malformed（可达但响应异常）一律暂停。
                    return match (e, call.degrade_policy) {
                        (
                            GateError::GateUnavailable(_),
                            DegradePolicy::PassthroughWithoutMemory,
                        ) => {
                            // P1-3：降级时丢弃已注入的载荷，用 pristine 请求重新渲染。
                            let mut degraded_call = ManagedCall {
                                scope_id: call.scope_id.clone(),
                                target_profile: call.target_profile.clone(),
                                request: pristine_request.clone(),
                                target_url: call.target_url.clone(),
                                headers: call.headers.clone(),
                                degrade_policy: DegradePolicy::Pause,
                                inject_context: false,
                                include_code: false,
                                capture_prefix: false,
                                conversation_id: None,
                                sediment: false,
                                prepared_contexts: Vec::new(),
                            };
                            degraded_call.request.stream = call.request.stream;
                            self.degraded_send(
                                &degraded_call,
                                renderer,
                                evidence,
                                execution,
                                recording,
                            )
                            .await
                        }
                        (e, _) => ManagedOutcome::GatePaused(classify_gate_error(e)),
                    };
                }
                Ok(result) => match result.decision {
                    CheckDecision::Allow => match validate_allow(
                        &result,
                        CheckPhase::BeforeSend,
                        self.require_privacy_coverage,
                    ) {
                        Ok((binding, deadline)) => Admission {
                            binding,
                            valid_until_ms: deadline,
                        },
                        Err(outcome) => return outcome,
                    },
                    CheckDecision::Block => {
                        return ManagedOutcome::Blocked {
                            reason_codes: result.reason_codes,
                        }
                    }
                    // 复用证明的依赖已变化（例如 Agent 改了被引用的代码文件）：丢弃该会话的重放，从注入前的
                    // 请求重新准备一次再送检，不把可恢复的过期交给客户端。每个请求最多刷新一次。
                    CheckDecision::Withhold
                        if !refreshed
                            && !call.prepared_contexts.is_empty()
                            && result
                                .reason_codes
                                .iter()
                                .any(|reason| reason == CONTEXT_REFRESH_REQUIRED) =>
                    {
                        refreshed = true;
                        if let Some((conversation, anchor)) =
                            call.conversation_id.as_deref().zip(anchor)
                        {
                            self.replays.forget(&call.scope_id, conversation, anchor);
                        }
                        call.request = pristine_request.clone();
                        call.prepared_contexts.clear();
                        continue;
                    }
                    CheckDecision::Withhold => {
                        return ManagedOutcome::withheld(result.reason_codes)
                    }
                },
            };
            break (payload, admission);
        };

        // P1-4：发送阶段。每次尝试前验有效期；确定性失败后重新走 check 拿新准入。
        let raw = {
            let mut current = admission;
            let mut attempt: u32 = 0;
            loop {
                attempt += 1;
                evidence.record_attempt(attempt);
                // P1-4：发送前验 valid_until（解析失败=0=立即过期，fail-closed）。
                if now_ms() >= current.valid_until_ms {
                    return ManagedOutcome::AdmissionExpired;
                }
                let send = AdmittedSend {
                    binding: current.binding.clone(),
                    payload: payload.bytes.clone(),
                    target_url: call.target_url.clone(),
                    headers: call.headers.clone(),
                    stream: call.request.stream,
                };
                let sent_binding = current.binding.clone();
                recording.begin_attempt();
                execution.model_send(ModelSendState::PossiblySent);
                match self.transport.send(send).await {
                    SendOutcome::Delivered(raw) => {
                        recording.received(&raw);
                        execution.model_send(ModelSendState::ResponseReceived);
                        break (raw, sent_binding);
                    }
                    SendOutcome::IncompleteResponse {
                        reason,
                        status,
                        response_first_byte_ms,
                    } => {
                        recording.incomplete_response(&reason, status, response_first_byte_ms);
                        return indeterminate_outcome(reason);
                    }
                    SendOutcome::Indeterminate(reason) => {
                        recording.indeterminate(&reason);
                        return indeterminate_outcome(reason);
                    }
                    SendOutcome::NotSent(reason) => {
                        recording.not_sent(&reason);
                        execution.model_send(ModelSendState::DefinitelyNotSent);
                        evidence.record_not_sent(attempt, &reason.to_string());
                        if attempt >= self.max_attempts.get() {
                            return ManagedOutcome::NotSent { attempts: attempt };
                        }
                        #[allow(unused_assignments)]
                        {
                            let _ = sent_binding;
                        }
                        // P1-4：重试前重新准入——撤销在这里被看见。
                        let recheck = ContextCheckRequest::BeforeSend {
                            scope_id: call.scope_id.clone(),
                            target_profile: call.target_profile.clone(),
                            payload: ModelJsonPayload::new(
                                content_headers(&call.headers, payload.content_type),
                                String::from_utf8(payload.bytes.to_vec()).unwrap_or_default(),
                            ),
                            prepared_contexts: call.prepared_contexts.clone(),
                        };
                        match self.gate.check(recheck).await {
                            Ok(r) if r.decision == CheckDecision::Allow => {
                                match validate_allow(
                                    &r,
                                    CheckPhase::BeforeSend,
                                    self.require_privacy_coverage,
                                ) {
                                    Ok((binding, deadline)) => {
                                        current = Admission {
                                            binding,
                                            valid_until_ms: deadline,
                                        };
                                    }
                                    Err(outcome) => return outcome,
                                }
                            }
                            Ok(r) if r.decision == CheckDecision::Block => {
                                return ManagedOutcome::Blocked {
                                    reason_codes: r.reason_codes,
                                }
                            }
                            Ok(r) => return ManagedOutcome::withheld(r.reason_codes),
                            Err(e) => return ManagedOutcome::GatePaused(classify_gate_error(e)),
                        }
                    }
                }
            }
        };

        // 释放阶段。
        let (raw, send_binding_for_release) = raw;
        let release_body = match String::from_utf8(raw.body.clone().to_vec()) {
            Ok(text) => text,
            Err(_) => {
                return ManagedOutcome::Withheld {
                    reason_codes: vec!["invalid_response_encoding".into()],
                    retryable: false,
                }
            }
        };
        let release_check = ContextCheckRequest::BeforeRelease {
            scope_id: call.scope_id.clone(),
            target_profile: call.target_profile.clone(),
            request_binding: send_binding_for_release,
            payload: ModelJsonPayload::new(
                BTreeMap::from([("content-type".to_string(), raw.content_type.clone())]),
                release_body.clone(),
            ),
            completion: pc_gateway_types::CONTEXT_CHECK_COMPLETION.into(),
        };
        let verdict = self.gate.check(release_check).await;
        recording.release_decision();
        match verdict {
            Err(e) => ManagedOutcome::GatePaused(classify_gate_error(e)),
            Ok(result) => match result.decision {
                CheckDecision::Allow => {
                    // Release requires its own valid admission under the configured mode.
                    let (_release_binding_used, _release_deadline_used) = match validate_allow(
                        &result,
                        CheckPhase::BeforeRelease,
                        self.require_privacy_coverage,
                    ) {
                        Ok(v) => v,
                        Err(outcome) => return outcome,
                    };
                    // P1-4：释放前验 release verdict 的 valid_until（fail-closed）。
                    let release_deadline = parse_rfc3339_ms(&result.valid_until).unwrap_or(0);
                    if now_ms() >= release_deadline {
                        return ManagedOutcome::AdmissionExpired;
                    }
                    // 每个用户回合只在终止响应（不再调用工具）时沉淀一次规范化记录；工具循环的中间响应不沉淀。
                    // 记录取自注入前的请求，不含准备结果（防止旧记忆被当成新用户证据）。
                    let transcript = call
                        .sediment
                        .then(|| {
                            turn_transcript(
                                &pristine_request,
                                &release_body,
                                renderer.message_list_key(),
                            )
                        })
                        .flatten();
                    if let Some(transcript) = transcript {
                        let digest = turn_digest(&call, &pristine_request).unwrap_or_else(|| {
                            result
                                .binding
                                .as_deref()
                                .map(simple_digest)
                                .unwrap_or_else(|| simple_digest(&release_body))
                        });
                        let source_id = format!("gw-turn-{digest}");
                        // 保留前缀供 Core 复用缓存抽取：句柄写在这一回合最后一段 Source 的元数据里。注入的准备块在
                        // 前缀里、不在 Source 里，Core 的抽取指令说明标记内的内容不是证据。
                        let prefix = self.keep_prefix(
                            &call,
                            &payload.bytes,
                            &release_body,
                            renderer.message_list_key(),
                        );
                        let count = transcript.parts.len();
                        for (index, content) in transcript.parts.into_iter().enumerate() {
                            let mut metadata = serde_json::json!({
                                "kind": "gateway_turn",
                                "target_profile": call.target_profile,
                                "powercontext_capture_coverage": transcript.coverage,
                            });
                            let part_id = if count > 1 {
                                metadata["turn"] = serde_json::json!({
                                    "source_id": source_id,
                                    "part": index + 1,
                                    "parts": count,
                                });
                                format!("{source_id}-{}of{count}", index + 1)
                            } else {
                                source_id.clone()
                            };
                            if index + 1 == count {
                                for (key, value) in prefix.iter().flatten() {
                                    metadata[*key] = value.clone();
                                }
                            }
                            let capture = CaptureContentSourceRequest {
                                scope_id: call.scope_id.clone(),
                                source_id: part_id,
                                content,
                                metadata: Some(metadata),
                            };
                            match self.gate.capture(capture).await {
                                Ok(_) => {}
                                // 同一回合已以不同内容沉淀（重试或重复的终止请求）：先写入的记录保留。
                                Err(GateError::GateRejected(409)) => {
                                    evidence.record_not_sent(0, "sediment_duplicate")
                                }
                                Err(_) => evidence.record_not_sent(0, "sediment_failed:gate_error"),
                            }
                        }
                        // P1-4 残留：capture 完成后复核——沉淀耗时可能跨越截止时间。
                        if now_ms() >= release_deadline {
                            return ManagedOutcome::AdmissionExpired;
                        }
                    }
                    recording.authorized();
                    ManagedOutcome::Released {
                        binding: result.binding,
                        status: raw.status,
                        content_type: raw.content_type.clone(),
                        body: raw.body,
                    }
                }
                CheckDecision::Block => {
                    self.transport
                        .cancel(&AdmittedSend {
                            binding: String::new(),
                            payload: bytes::Bytes::new(),
                            target_url: call.target_url.clone(),
                            headers: call.headers.clone(),
                            stream: call.request.stream,
                        })
                        .await;
                    ManagedOutcome::Blocked {
                        reason_codes: result.reason_codes,
                    }
                }
                CheckDecision::Withhold => ManagedOutcome::withheld(result.reason_codes),
            },
        }
    }

    /// Core 凭句柄取回的前缀（同一 Scope、未过期）。
    pub fn prefix(&self, handle: &str, scope_id: &str) -> Option<PrefixEntry> {
        self.prefixes.get(handle, scope_id, now_ms())
    }

    /// 保留一个 Responses 回合的终止请求与响应输出，返回要写入 Source 元数据的句柄和前缀规模。只用于可信会话。
    /// 规模取自终止响应的用量：输入部分在抽取时命中缓存，输出部分作为新输入计费，Core 据此判断复用前缀是否比
    /// 普通抽取便宜。
    fn keep_prefix(
        &self,
        call: &ManagedCall,
        sent: &bytes::Bytes,
        release_body: &str,
        list_key: &str,
    ) -> Option<Vec<(&'static str, serde_json::Value)>> {
        if !call.capture_prefix || list_key != "input" {
            return None;
        }
        let conversation_id = call.conversation_id.clone()?;
        let response: serde_json::Value = serde_json::from_str(release_body).ok()?;
        let response_output = response.get("output")?.as_array()?.clone();
        let usage = response.get("usage");
        let tokens = |field: &str| usage.and_then(|u| u.get(field)).and_then(|v| v.as_u64());
        let handle = self.prefixes.add(
            PrefixEntry {
                scope_id: call.scope_id.clone(),
                target_profile: call.target_profile.clone(),
                conversation_id,
                request_body: sent.clone(),
                response_output,
            },
            now_ms(),
        )?;
        let mut fields = vec![("prefix_handle", handle.into())];
        if let (Some(input), Some(output)) = (tokens("input_tokens"), tokens("output_tokens")) {
            fields.push(("prefix_input_tokens", input.into()));
            fields.push(("prefix_output_tokens", output.into()));
        }
        Some(fields)
    }

    /// 有可信会话 ID 时：同一消息锚点（工具续写、重试）精确重放已准备的内容与绑定，不再检索；
    /// 新锚点（新的用户消息）准备一次。没有会话 ID 时每个请求各自准备，不继承任何旧注入。
    /// 准备失败不写入，下一次请求重试，且不阻塞本次请求；空结果同样重放。
    async fn enrich(
        &self,
        call: &mut ManagedCall,
        anchor: Option<u64>,
        list_key: &str,
        evidence: &dyn EvidenceSink,
    ) {
        let replay = call.conversation_id.clone().zip(anchor);
        let cached = replay.as_ref().and_then(|(conversation, anchor)| {
            self.replays
                .get(&call.scope_id, conversation, *anchor, now_ms())
        });
        let prepared = match cached {
            Some(prepared) => prepared,
            None => {
                let Some((prepared, expires_at)) = self.prepare_turn(call, evidence).await else {
                    return;
                };
                match &replay {
                    Some((conversation, anchor)) => {
                        let now = now_ms();
                        let expires = expires_at
                            .as_deref()
                            .and_then(parse_rfc3339_ms)
                            .map_or(now + MAX_REPLAY_MS, |at| at.min(now + MAX_REPLAY_MS));
                        self.replays.put_if_absent(
                            &call.scope_id,
                            conversation,
                            *anchor,
                            prepared,
                            expires,
                        )
                    }
                    None => prepared,
                }
            }
        };
        let Some(PreparedBlock { block, binding }) = prepared else {
            return;
        };
        self.inject(call, &block, &binding, list_key);
    }

    /// 调用 prepare：失败返回 None（不缓存、不阻塞主请求）；
    /// 成功返回（可注入的块，复用绑定到期时间）。
    async fn prepare_turn(
        &self,
        call: &ManagedCall,
        evidence: &dyn EvidenceSink,
    ) -> Option<(Option<PreparedBlock>, Option<String>)> {
        let query = last_user_text(&call.request)?;
        let prepared = match self
            .gate
            .prepare(PrepareContextRequest {
                scope_id: call.scope_id.clone(),
                query,
                max_bytes: 8192,
                include_reuse: true,
                include_code: call.include_code,
                // P2 review：限定 memory-only（#2313 reuse 只支持 atomic-memory），
                // 防止 Experience/Topic 加入后 check 拒绝导致注入静默失效。
                assembly: Some(pc_gateway_types::ContextAssembly::memory_only()),
            })
            .await
        {
            Ok(p) => p,
            Err(_) => {
                evidence.record_not_sent(0, "prepare_failed");
                tracing::warn!("prepare failed; injecting nothing");
                return None;
            }
        };
        let expires_at = prepared.expires_at().map(str::to_owned);
        let block = match (prepared.content(), prepared.binding()) {
            (Some(content), Some(binding)) if !content.is_empty() => Some(PreparedBlock {
                block: content.to_owned(),
                binding: binding.to_owned(),
            }),
            _ => None,
        };
        Some((block, expires_at))
    }

    /// 把准备好的块注入最后一条用户消息，并声明它在渲染后请求体中的位置。
    fn inject(&self, call: &mut ManagedCall, block: &str, binding: &str, list_key: &str) {
        let Some(index) = last_user_index(&call.request) else {
            return;
        };
        let Some(message) = call.request.messages.get_mut(index) else {
            return;
        };
        // Core accepts Responses prepared-context locations only in input_text parts.
        // Normalize only when injecting, preserving uninjected and degraded payloads.
        if list_key == "input" {
            if let [serde_json::Value::String(text)] = message.content.as_slice() {
                message.content = vec![serde_json::json!({"type": "input_text", "text": text})];
            }
        }
        match message.content.as_slice() {
            [serde_json::Value::String(s)] => {
                let new_text = format!("{block}\n\n{s}");
                message.content = vec![serde_json::Value::String(new_text)];
            }
            // typed parts：只修改第一个 text 部件注入记忆（不重复注入），
            // 保留所有非文本部件（image_url 等）——移除非文本部件会让 Core
            // 看不到不支持的内容（安全缺陷），重复注入会膨胀 payload（审计缺陷）。
            parts if !parts.is_empty() => {
                let first_text_idx = parts.iter().position(is_text_part);
                let Some(text_idx) = first_text_idx else {
                    return;
                };
                let text = parts[text_idx]
                    .get("text")
                    .and_then(|t| t.as_str())
                    .unwrap_or("");
                let new_parts: Vec<serde_json::Value> = parts
                    .iter()
                    .enumerate()
                    .map(|(j, p)| {
                        if j == text_idx {
                            let mut np = p.clone();
                            np["text"] = serde_json::json!(format!("{block}\n\n{text}"));
                            np
                        } else {
                            p.clone() // 其余部件（含非第一个 text）原样保留
                        }
                    })
                    .collect();
                message.content = new_parts;
                // typed parts 的 prepared_contexts 指针必须指向具体 text 部件
                // （/messages/{i}/content 是纯字符串形态的指针，数组形态用
                //  /messages/{i}/content/{j}/text——PC 契约的两种合法形态）。
                call.prepared_contexts.push(PreparedContextLocation {
                    binding: binding.to_string(),
                    location: ModelJsonTextLocation {
                        schema: pc_gateway_types::MODEL_JSON_TEXT_LOCATION_SCHEMA.to_string(),
                        pointer: format!("/{list_key}/{index}/content/{text_idx}/text"),
                        start_byte: 0,
                        end_byte: block.len() as u64,
                    },
                });
                return;
            }
            _ => return,
        }
        call.prepared_contexts.push(PreparedContextLocation {
            binding: binding.to_string(),
            location: ModelJsonTextLocation {
                schema: pc_gateway_types::MODEL_JSON_TEXT_LOCATION_SCHEMA.to_string(),
                pointer: format!("/{list_key}/{index}/content"),
                start_byte: 0,
                end_byte: block.len() as u64,
            },
        });
    }

    /// P1-3：降级直通用 pristine 请求（不含注入）重新渲染再发送。
    async fn degraded_send<R: WireRenderer>(
        &self,
        call: &ManagedCall,
        renderer: &R,
        evidence: &dyn EvidenceSink,
        execution: &mut Execution,
        recording: &mut CallRecorder<'_>,
    ) -> ManagedOutcome {
        evidence.record_not_sent(0, "degraded_passthrough");
        let payload = match renderer.render(&call.request, None) {
            Ok(p) => p,
            Err(e) => {
                return ManagedOutcome::Blocked {
                    reason_codes: vec![format!("render_failed:{e}")],
                }
            }
        };
        if payload.bytes.len() > self.max_input_bytes {
            return ManagedOutcome::Blocked {
                reason_codes: vec!["request_payload_too_large".into()],
            };
        }
        let send = AdmittedSend {
            binding: "degraded-passthrough".into(),
            payload: payload.bytes,
            target_url: call.target_url.clone(),
            headers: call.headers.clone(),
            stream: call.request.stream,
        };
        recording.begin_attempt();
        execution.model_send(ModelSendState::PossiblySent);
        match self.transport.send(send).await {
            SendOutcome::Delivered(raw) => {
                recording.received(&raw);
                execution.model_send(ModelSendState::ResponseReceived);
                ManagedOutcome::Degraded {
                    status: raw.status,
                    content_type: raw.content_type,
                    body: raw.body,
                }
            }
            SendOutcome::IncompleteResponse {
                reason,
                status,
                response_first_byte_ms,
            } => {
                recording.incomplete_response(&reason, status, response_first_byte_ms);
                indeterminate_outcome(reason)
            }
            SendOutcome::Indeterminate(reason) => {
                recording.indeterminate(&reason);
                indeterminate_outcome(reason)
            }
            SendOutcome::NotSent(reason) => {
                recording.not_sent(&reason);
                execution.model_send(ModelSendState::DefinitelyNotSent);
                evidence.record_not_sent(1, &reason.to_string());
                ManagedOutcome::NotSent { attempts: 1 }
            }
        }
    }
}

// Capacity failure withholds the incomplete body without clearing possible billing.
// Other uncertain sends keep their existing indeterminate terminal; none is retried.
fn indeterminate_outcome(reason: SendIndeterminateReason) -> ManagedOutcome {
    match reason {
        SendIndeterminateReason::BufferLimitExceeded => ManagedOutcome::Withheld {
            reason_codes: vec!["response_buffer_limit_exceeded".into()],
            retryable: false,
        },
        _ => ManagedOutcome::SendIndeterminate,
    }
}

fn content_headers(headers: &[(String, String)], content_type: &str) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    out.insert("content-type".to_string(), content_type.to_string());
    for (k, v) in headers {
        let lower = k.to_ascii_lowercase();
        if matches!(lower.as_str(), "authorization" | "x-api-key" | "cookie") {
            continue;
        }
        out.insert(lower, v.clone());
    }
    out
}

/// Validate phase, binding and deadline independently of privacy enforcement.
/// Complete privacy coverage is required unless the trusted Core explicitly
/// advertises non-enforcing operation.
fn validate_allow(
    result: &ContextCheckResult,
    expected_phase: CheckPhase,
    require_privacy_coverage: bool,
) -> Result<(String, u64), ManagedOutcome> {
    // 1. schema 必须匹配
    if result.schema != pc_gateway_types::CONTEXT_CHECK_SCHEMA {
        return Err(ManagedOutcome::GatePaused(
            "malformed_verdict: schema mismatch".into(),
        ));
    }
    // 2. phase 必须与请求阶段一致
    if result.phase != expected_phase {
        return Err(ManagedOutcome::GatePaused(format!(
            "malformed_verdict: phase mismatch: expected {expected_phase:?}, got {:?}",
            result.phase
        )));
    }
    // 3. Enforcing admission requires complete privacy coverage.
    if require_privacy_coverage && !result.coverage.complete {
        return Err(ManagedOutcome::GatePaused(
            "malformed_verdict: incomplete coverage on allow".into(),
        ));
    }
    // 4. binding 必须非空（不得用 missing-binding 替代）
    let binding = result
        .binding
        .as_deref()
        .filter(|b| !b.is_empty())
        .ok_or_else(|| {
            ManagedOutcome::GatePaused("malformed_verdict: allow without binding".into())
        })?;
    // 5. valid_until 必须可解析（fail-closed 在 unwrap_or(0) 已处理）
    let deadline = parse_rfc3339_ms(&result.valid_until).unwrap_or(0);
    Ok((binding.to_string(), deadline))
}

fn classify_gate_error(e: GateError) -> String {
    match e {
        GateError::GateUnavailable(_) => "gate_unavailable".into(),
        GateError::Malformed(_) => "malformed_gate_response".into(),
        GateError::GateRejected(status) => format!("gate rejected: HTTP {status}"),
    }
}

/// 文本内容部件：Chat 为 `text`，Responses 的用户输入为 `input_text`。
fn is_text_part(part: &serde_json::Value) -> bool {
    matches!(
        part.get("type").and_then(|t| t.as_str()),
        Some("text" | "input_text")
    )
}

fn last_user_index(request: &CanonicalRequest) -> Option<usize> {
    request
        .messages
        .iter()
        .rposition(|m| matches!(m.role, pc_gateway_protocol::Role::User))
}

fn last_user_text(request: &CanonicalRequest) -> Option<String> {
    let i = last_user_index(request)?;
    match request.messages[i].content.as_slice() {
        [serde_json::Value::String(s)] => Some(s.clone()),
        // typed content parts：[{"type":"text"或"input_text","text":"..."},...] → 拼接 text 字段。
        // 这是 Chat 与 Responses 的合法形态；不处理会导致沉淀 user 为空 + 注入被跳过。
        parts if !parts.is_empty() => {
            let texts: Vec<String> = parts
                .iter()
                .filter(|p| is_text_part(p))
                .filter_map(|p| p.get("text").and_then(|t| t.as_str()).map(String::from))
                .collect();
            if texts.is_empty() {
                None
            } else {
                Some(texts.join("\n"))
            }
        }
        _ => None,
    }
}

/// 一个用户回合的稳定标识：Scope、可信会话 ID 与截至最后一条用户消息的注入前消息。同一回合的重试、重复或并发的
/// 终止请求得到同一个 Source ID。没有可信会话时返回 `None`：不同会话的相同消息不能合并，调用方退回到每次放行
/// 各自的标识。
fn turn_digest(call: &ManagedCall, pristine: &CanonicalRequest) -> Option<String> {
    let conversation = call.conversation_id.as_deref()?;
    let last_user = pristine
        .messages
        .iter()
        .rposition(|m| matches!(m.role, pc_gateway_protocol::Role::User))?;
    let messages = serde_json::to_string(&pristine.messages[..=last_user]).ok()?;
    Some(simple_digest(&format!(
        "{}\n{}\n{}\n{messages}",
        call.scope_id.len(),
        call.scope_id,
        conversation
    )))
}

fn simple_digest(input: &str) -> String {
    let mut hash: u64 = 0xcbf29ce484222325;
    for b in input.as_bytes() {
        hash ^= *b as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}
