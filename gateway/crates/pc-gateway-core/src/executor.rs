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

use crate::{GateClient, GateError};
use pc_gateway_evidence::EvidenceSink;
use pc_gateway_protocol::{CanonicalRequest, WireRenderer};
use pc_gateway_transport::{AdmittedSend, SendOutcome, Transport};
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

pub struct Executor<G, T> {
    gate: G,
    transport: T,
    max_attempts: NonZeroU32,
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
        }
    }

    pub async fn run<R: WireRenderer>(
        &self,
        call: ManagedCall,
        renderer: &R,
        evidence: &dyn EvidenceSink,
    ) -> ManagedOutcome {
        let mut call = call;
        // P1-3：enrich 之前快照原始请求。降级路径必须用这份（不含注入内容）
        // 重新渲染，否则 prepare 召回的私密记忆会在未经任何检查的情况下外发。
        let pristine_request = call.request.clone();
        let pristine_user_text = last_user_text(&pristine_request).unwrap_or_default();

        if call.inject_context {
            self.enrich(&mut call, evidence).await;
        }

        let payload = match renderer.render(&call.request, None) {
            Ok(p) => p,
            Err(e) => {
                return ManagedOutcome::Blocked {
                    reason_codes: vec![format!("render_failed:{e}")],
                }
            }
        };
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
                    (GateError::GateUnavailable(_), DegradePolicy::PassthroughWithoutMemory) => {
                        // P1-3：降级时丢弃已注入的载荷，用 pristine 请求重新渲染。
                        let mut degraded_call = ManagedCall {
                            scope_id: call.scope_id.clone(),
                            target_profile: call.target_profile.clone(),
                            request: pristine_request,
                            target_url: call.target_url.clone(),
                            headers: call.headers.clone(),
                            degrade_policy: DegradePolicy::Pause,
                            inject_context: false,
                            sediment: false,
                            prepared_contexts: Vec::new(),
                        };
                        degraded_call.request.stream = call.request.stream;
                        self.degraded_send(&degraded_call, renderer, evidence).await
                    }
                    (e, _) => ManagedOutcome::GatePaused(classify_gate_error(e)),
                };
            }
            Ok(result) => match result.decision {
                CheckDecision::Allow => match validate_allow(&result, CheckPhase::BeforeSend) {
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
                CheckDecision::Withhold => {
                    return ManagedOutcome::Withheld {
                        reason_codes: result.reason_codes,
                    }
                }
            },
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
                match self.transport.send(send).await {
                    SendOutcome::Delivered(raw) => {
                        break (raw, sent_binding);
                    }
                    SendOutcome::Indeterminate(_) => return ManagedOutcome::SendIndeterminate,
                    SendOutcome::NotSent(reason) => {
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
                                match validate_allow(&r, CheckPhase::BeforeSend) {
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
                            Ok(r) => {
                                return ManagedOutcome::Withheld {
                                    reason_codes: r.reason_codes,
                                }
                            }
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
        match self.gate.check(release_check).await {
            Err(e) => ManagedOutcome::GatePaused(classify_gate_error(e)),
            Ok(result) => match result.decision {
                CheckDecision::Allow => {
                    // P2 review：验证完整 allow（schema/phase/coverage/binding）。
                    let (_release_binding_used, _release_deadline_used) =
                        match validate_allow(&result, CheckPhase::BeforeRelease) {
                            Ok(v) => v,
                            Err(outcome) => return outcome,
                        };
                    // P1-4：释放前验 release verdict 的 valid_until（fail-closed）。
                    let release_deadline = parse_rfc3339_ms(&result.valid_until).unwrap_or(0);
                    if now_ms() >= release_deadline {
                        return ManagedOutcome::AdmissionExpired;
                    }
                    if call.sediment {
                        let transcript = serde_json::json!({
                            "kind": "gateway_turn",
                            // P2 review：沉淀的 user 字段必须用原始（pre-injection）用户文本，
                            // 不含 enrich 注入的记忆块（防止旧记忆被当成新用户证据）。
                            "user": pristine_user_text.clone(),
                            "assistant": release_body.clone(),
                            "model": call.request.model,
                        });
                        let digest = result
                            .binding
                            .as_deref()
                            .map(simple_digest)
                            .unwrap_or_else(|| simple_digest(&release_body));
                        let capture = CaptureContentSourceRequest {
                            scope_id: call.scope_id.clone(),
                            source_id: format!("gw-turn-{digest}"),
                            content: transcript.to_string(),
                            metadata: Some(serde_json::json!({
                                "kind": "gateway_turn",
                                "target_profile": call.target_profile,
                            })),
                        };
                        if let Err(e) = self.gate.capture(capture).await {
                            evidence.record_not_sent(0, &format!("sediment_failed:{e}"));
                        }
                        // P1-4 残留：capture 完成后复核——沉淀耗时可能跨越截止时间。
                        if now_ms() >= release_deadline {
                            return ManagedOutcome::AdmissionExpired;
                        }
                    }
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
                CheckDecision::Withhold => ManagedOutcome::Withheld {
                    reason_codes: result.reason_codes,
                },
            },
        }
    }

    async fn enrich(&self, call: &mut ManagedCall, evidence: &dyn EvidenceSink) {
        let Some(query) = last_user_text(&call.request) else {
            return;
        };
        let prepared = match self
            .gate
            .prepare(PrepareContextRequest {
                scope_id: call.scope_id.clone(),
                query,
                max_bytes: 8192,
                include_reuse: true,
                // P2 review：限定 memory-only（#2313 reuse 只支持 atomic-memory），
                // 防止 Experience/Topic 加入后 check 拒绝导致注入静默失效。
                assembly: Some(pc_gateway_types::ContextAssembly::memory_only()),
            })
            .await
        {
            Ok(p) => p,
            Err(e) => {
                evidence.record_not_sent(0, &format!("prepare_failed:{e}"));
                tracing::warn!(error = %e, "prepare failed; injecting nothing");
                return;
            }
        };
        let (Some(content), Some(binding)) = (prepared.content(), prepared.binding()) else {
            return;
        };
        if content.is_empty() {
            return;
        }
        let block = content.to_string();
        let Some(index) = last_user_index(&call.request) else {
            return;
        };
        let Some(message) = call.request.messages.get_mut(index) else {
            return;
        };
        let original = match message.content.as_slice() {
            [serde_json::Value::String(s)] => s.clone(),
            // typed parts：提取文本注入，保留原有 typed 结构。
            parts if !parts.is_empty() => {
                let texts: Vec<String> = parts
                    .iter()
                    .filter_map(|p| p.get("text").and_then(|t| t.as_str()).map(String::from))
                    .collect();
                if texts.is_empty() {
                    return;
                }
                texts.join("\n")
            }
            _ => return,
        };
        // 注入后恢复为单字符串（与 typed parts 请求兼容的线格式）
        let new_text = format!("{block}\n\n{original}");
        message.content = vec![serde_json::Value::String(new_text)];
        call.prepared_contexts.push(PreparedContextLocation {
            binding: binding.to_string(),
            location: ModelJsonTextLocation {
                schema: pc_gateway_types::MODEL_JSON_TEXT_LOCATION_SCHEMA.to_string(),
                pointer: format!("/messages/{index}/content"),
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
        let send = AdmittedSend {
            binding: "degraded-passthrough".into(),
            payload: payload.bytes,
            target_url: call.target_url.clone(),
            headers: call.headers.clone(),
            stream: call.request.stream,
        };
        match self.transport.send(send).await {
            SendOutcome::Delivered(raw) => ManagedOutcome::Degraded {
                status: raw.status,
                content_type: raw.content_type,
                body: raw.body,
            },
            SendOutcome::Indeterminate(_) => ManagedOutcome::SendIndeterminate,
            SendOutcome::NotSent(reason) => {
                evidence.record_not_sent(1, &reason.to_string());
                ManagedOutcome::NotSent { attempts: 1 }
            }
        }
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

/// 验证 allow 响应的完整性：schema/phase/coverage/binding 四项缺一不可。
/// 契约："Only allow with complete coverage permits the corresponding phase."
/// 畸形对端返回不完整 allow 时必须暂停（fail-closed），不得替代或放行。
fn validate_allow(
    result: &ContextCheckResult,
    expected_phase: CheckPhase,
) -> Result<(String, u64), ManagedOutcome> {
    // 1. schema 必须匹配
    if result.schema != pc_gateway_types::CONTEXT_CHECK_SCHEMA {
        return Err(ManagedOutcome::GatePaused(format!(
            "malformed_verdict: schema mismatch: {}",
            result.schema
        )));
    }
    // 2. phase 必须与请求阶段一致
    if result.phase != expected_phase {
        return Err(ManagedOutcome::GatePaused(format!(
            "malformed_verdict: phase mismatch: expected {expected_phase:?}, got {:?}",
            result.phase
        )));
    }
    // 3. coverage.complete 必须为 true
    if !result.coverage.complete {
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
        GateError::GateUnavailable(msg) | GateError::Malformed(msg) => msg,
        GateError::GateRejected(status) => format!("gate rejected: HTTP {status}"),
    }
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
        // typed content parts：[{"type":"text","text":"..."},...] → 拼接 text 字段。
        // 这是 OpenAI Chat 的合法形态；不处理会导致沉淀 user 为空 + 注入被跳过。
        parts if !parts.is_empty() => {
            let texts: Vec<String> = parts
                .iter()
                .filter_map(|p| {
                    p.get("type")
                        .and_then(|t| t.as_str())
                        .filter(|t| t == &"text")
                        .and_then(|_| p.get("text"))
                        .and_then(|t| t.as_str())
                        .map(String::from)
                })
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
