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

use crate::{GateClient, GateError};
use pc_gateway_evidence::EvidenceSink;
use pc_gateway_protocol::{CanonicalRequest, WireRenderer};
use pc_gateway_transport::{AdmittedSend, BoundedRetry, RetryOutcome, SendOutcome, Transport};
use pc_gateway_types::{
    CaptureContentSourceRequest, CheckDecision, ContextCheckRequest, ContextCheckResult,
    ModelJsonPayload, ModelJsonTextLocation, PrepareContextRequest, PreparedContextLocation,
};
use std::collections::BTreeMap;
use std::num::NonZeroU32;

/// Gate 不可用时的策略（PR #2199 故障语义）：
/// Pause —— 必需强制检查不可用：不发送（默认，安全立场）；
/// PassthroughWithoutMemory —— 增强类调用可降级（仅限 capabilities.enforcing=false）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum DegradePolicy {
    #[default]
    Pause,
    PassthroughWithoutMemory,
}

/// 宿主交给执行器的完整输入：已授权历史 + 当前请求 + 目标。
pub struct ManagedCall {
    pub scope_id: String,
    pub target_profile: String,
    pub request: CanonicalRequest,
    pub target_url: String,
    /// 目标协议要求的头部（凭据由受控配置解析，不经请求字段）。
    pub headers: Vec<(String, String)>,
    pub degrade_policy: DegradePolicy,
    /// 是否调 prepare 注入上下文（v1 仅 chat 格式；PC 决定内容，网关只放置）。
    pub inject_context: bool,
    /// 是否在过检后同步沉淀本轮对话（capture_content_source，毫秒级落库）。
    pub sediment: bool,
    /// 注入块位置声明，随 before_send 送检。
    pub prepared_contexts: Vec<PreparedContextLocation>,
}

/// 执行结果：要么是过检放行的内容，要么是显式拒绝/不确定。
/// 不存在“静默成功”或“伪装成空结果”的路径。
#[derive(Debug)]
pub enum ManagedOutcome {
    /// before_release 的 allow：绑定与过检内容一起返回。
    Released {
        binding: Option<String>,
        status: u16,
        content_type: String,
        body: bytes::Bytes,
    },
    /// Core 明确拒绝（两阶段的 block），携带无正文原因类别。
    Blocked { reason_codes: Vec<String> },
    /// Core 暂缓。PR #2264 的 withhold 不含渲染计划，一律按终态处理。
    Withheld { reason_codes: Vec<String> },
    /// 可能已到达上游；不自动付费重发。
    SendIndeterminate,
    /// 全部尝试确定性未送达。
    NotSent { attempts: u32 },
    /// Gate 不可用：暂停，不是失败放行。
    GatePaused(String),
    /// 无记忆直通降级：未经检查发送（仅限未启用强制规则的部署），
    /// 响应头/证据必须显式标记，不得伪装成过检结果。
    Degraded {
        status: u16,
        content_type: String,
        body: bytes::Bytes,
    },
}

pub struct Executor<G, T> {
    gate: G,
    retry: BoundedRetry<T>,
}

impl<G, T> Executor<G, T>
where
    G: GateClient,
    T: Transport,
{
    pub fn new(gate: G, transport: T, max_attempts: NonZeroU32) -> Self {
        Self {
            gate,
            retry: BoundedRetry::new(transport, max_attempts),
        }
    }

    /// 一次托管调用。渲染器按请求传入：入口格式即上游格式（同格式进出）。
    pub async fn run<R: WireRenderer>(
        &self,
        call: ManagedCall,
        renderer: &R,
        evidence: &dyn EvidenceSink,
    ) -> ManagedOutcome {
        let mut call = call;
        if call.inject_context {
            // 注入是增强类调用：失败不阻塞主链路（不注入、不声明区间）。
            self.enrich(&mut call, evidence).await;
        }
        // 渲染完整最终字节（不含缓存计划：PR #2264 的 withhold 未携带渲染计划）。
        let payload = match renderer.render(&call.request, None) {
            Ok(p) => p,
            Err(e) => {
                return ManagedOutcome::Blocked {
                    reason_codes: vec![format!("render_failed:{e}")],
                }
            }
        };
        // 送检内容必须与发送字节严格一致：拒绝有损转换（防“检查≠发送”缝隙）。
        // 渲染器输出本应恒为合法 UTF-8，非法即契约违约，阻断而非降级。
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

        let binding = match self.gate.check(check).await {
            Err(e) => {
                // #2199 语义：降级仅适用于 Core「不可用」。Malformed 表示 Core 可达但
                // 响应异常（版本错乱/被篡改/代理污染）——恰恰最不该绕过检查，一律暂停。
                return match (e, call.degrade_policy) {
                    (GateError::GateUnavailable(_), DegradePolicy::PassthroughWithoutMemory) => {
                        self.degraded_send(&call, payload.bytes, evidence).await
                    }
                    (e, _) => ManagedOutcome::GatePaused(classify_gate_error(e)),
                };
            }
            Ok(result) => match result.decision {
                CheckDecision::Allow => unwrap_binding(result),
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

        let send = AdmittedSend {
            binding,
            payload: payload.bytes,
            target_url: call.target_url.clone(),
            headers: call.headers.clone(),
            stream: call.request.stream,
        };
        let send_ref = send.clone();

        let raw = match self.retry.send_with_retry(&send, evidence).await {
            RetryOutcome::AllNotSent { attempts } => return ManagedOutcome::NotSent { attempts },
            RetryOutcome::Done(SendOutcome::Indeterminate(_)) => {
                return ManagedOutcome::SendIndeterminate
            }
            RetryOutcome::Done(SendOutcome::NotSent(_)) => {
                unreachable!("BoundedRetry never returns Done(NotSent)")
            }
            RetryOutcome::Done(SendOutcome::Delivered(raw)) => raw,
        };

        // strict：完整原始响应先过 before_release，通过前不释放任何内容。
        // 上游响应不受我们控制：非 UTF-8 字节不做有损转换送检（那会导致
        // “批准的是替换后文本、交付的是原始字节”的保真缺口），直接不放行。
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
            request_binding: send_ref.binding.clone(),
            payload: ModelJsonPayload::new(
                // 契约要求 headers 至少 1 项；携带上游实际 Content-Type。
                BTreeMap::from([("content-type".to_string(), raw.content_type.clone())]),
                release_body.clone(),
            ),
            completion: pc_gateway_types::CONTEXT_CHECK_COMPLETION.into(),
        };
        match self.gate.check(release_check).await {
            Err(e) => ManagedOutcome::GatePaused(classify_gate_error(e)),
            Ok(result) => match result.decision {
                CheckDecision::Allow => {
                    // 沉淀：同步、best-effort（失败仅记证据，不影响交付）。
                    // 内容只含本轮 user 文本与过检响应——被拦/降级轮次不沉淀。
                    if call.sediment {
                        let transcript = serde_json::json!({
                            "kind": "gateway_turn",
                            "user": call.request.messages.iter()
                                .rev().find(|m| matches!(m.role, pc_gateway_protocol::Role::User))
                                .and_then(|m| match m.content.as_slice() {
                                    [serde_json::Value::String(s)] => Some(s.clone()),
                                    _ => None,
                                }).unwrap_or_default(),
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
                    }
                    ManagedOutcome::Released {
                        binding: result.binding,
                        status: raw.status,
                        content_type: raw.content_type.clone(),
                        body: raw.body,
                    }
                }
                CheckDecision::Block => {
                    // 不释放任何内容；尽力取消上游，只保留无正文失败证据。
                    self.retry.transport().cancel(&send_ref).await;
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

    /// 注入（放置与声明，非检索）：调 prepare → 标准块包裹 → 拼进最后一条
    /// user 消息头部 → 计算字节区间。任一步失败则保持原样（不注入、不声明）。
    async fn enrich(&self, call: &mut ManagedCall, evidence: &dyn EvidenceSink) {
        // 取最后一条 user 消息的纯文本作为检索词；非单字符串 content 不注入（v1）。
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
            })
            .await
        {
            Ok(p) => p,
            Err(e) => {
                evidence.record_not_sent(0, &format!("prepare_failed:{e}"));
                return;
            }
        };
        let (Some(content), Some(binding)) = (prepared.content(), prepared.binding()) else {
            return; // empty：不注入任何标记（空块会污染前缀且无 binding 可声明）
        };
        if content.is_empty() {
            return;
        }
        // PC 返回的 content 已是标准注入格式（信任前言 + 标记包裹块）。
        // 必须原样注入：check 会校验区间字节摘要 == sha256(content)，
        // 网关任何增删（包括再包裹）都会导致 digest 不匹配被暂缓。
        let block = content.to_string();
        let Some(index) = last_user_index(&call.request) else {
            return;
        };
        let Some(message) = call.request.messages.get_mut(index) else {
            return;
        };
        let original = match message.content.as_slice() {
            [serde_json::Value::String(s)] => s.clone(),
            _ => return,
        };
        let new_text = format!("{block}\n\n{original}");
        message.content = vec![serde_json::Value::String(new_text)];
        call.prepared_contexts.push(PreparedContextLocation {
            binding: binding.to_string(),
            location: ModelJsonTextLocation {
                schema: pc_gateway_types::MODEL_JSON_TEXT_LOCATION_SCHEMA.to_string(),
                // chat 线格式：content 为纯字符串，指针用无部件形态。
                pointer: format!("/messages/{index}/content"),
                start_byte: 0,
                // UTF-8 字节数（非字符数）；区间必须落在字符边界（块整体是合法 UTF-8，天然满足）。
                end_byte: block.len() as u64,
            },
        });
    }

    /// 降级直通：未经检查发送。显式标记，不做释放检查。
    async fn degraded_send(
        &self,
        call: &ManagedCall,
        payload: bytes::Bytes,
        evidence: &dyn EvidenceSink,
    ) -> ManagedOutcome {
        evidence.record_not_sent(0, "degraded_passthrough");
        let send = AdmittedSend {
            binding: "degraded-passthrough".into(),
            payload,
            target_url: call.target_url.clone(),
            headers: call.headers.clone(),
            stream: call.request.stream,
        };
        match self.retry.send_with_retry(&send, evidence).await {
            RetryOutcome::AllNotSent { attempts } => ManagedOutcome::NotSent { attempts },
            RetryOutcome::Done(SendOutcome::Indeterminate(_)) => ManagedOutcome::SendIndeterminate,
            RetryOutcome::Done(SendOutcome::NotSent(_)) => {
                unreachable!("BoundedRetry never returns Done(NotSent)")
            }
            RetryOutcome::Done(SendOutcome::Delivered(raw)) => ManagedOutcome::Degraded {
                status: raw.status,
                content_type: raw.content_type,
                body: raw.body,
            },
        }
    }
}

fn unwrap_binding(result: ContextCheckResult) -> String {
    result
        .binding
        .unwrap_or_else(|| "missing-binding".to_string())
}

/// 载荷头只保留内容/协议头；凭据与传输覆盖头禁止进 check 载荷。
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

fn classify_gate_error(e: GateError) -> String {
    match e {
        GateError::GateUnavailable(msg) | GateError::Malformed(msg) => msg,
    }
}

/// 最后一条 user 消息下标（渲染保持消息顺序，与线格式下标一致）。
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
        _ => None,
    }
}

/// 稳定短摘要（幂等键用；非安全用途，无需密码学强度）。
fn simple_digest(input: &str) -> String {
    // FNV-1a 64 位，输出 16 hex —— 足够做 source_id 的幂等区分。
    let mut hash: u64 = 0xcbf29ce484222325;
    for b in input.as_bytes() {
        hash ^= *b as u64;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}
