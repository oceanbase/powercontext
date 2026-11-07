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

//! 模型入口：OpenAI 兼容 `/gateway/v1/chat/completions`。
//! 客户端只改 Base URL 与 API Key 即接入（PR #2199 产品形态）。

use crate::evidence_body::FirstContentOffset;
use crate::{proxy::SharedProxy, AppState, Injection};
use axum::body::Bytes;
use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use pc_gateway_core::{ManagedCall, ManagedOutcome, RequestControl};
use pc_gateway_protocol::{
    first_model_content_offset, CanonicalMessage, CanonicalRequest, OutputLimits, OutputProtocol,
    Role,
};
use serde::Deserialize;
use std::sync::Arc;

#[derive(Debug, Deserialize)]
struct OpenAiChatRequest {
    model: String,
    messages: Vec<OpenAiMessage>,
    #[serde(default)]
    tools: Vec<serde_json::Value>,
    #[serde(default)]
    max_tokens: Option<u32>,
    /// 优先于 max_tokens（OpenAI 新字段；两者并存时以此为准）。
    #[serde(default)]
    max_completion_tokens: Option<u32>,
    #[serde(default)]
    response_format: Option<serde_json::Value>,
    #[serde(default)]
    tool_choice: Option<serde_json::Value>,
    #[serde(default)]
    stream: Option<bool>,
    #[serde(flatten)]
    extra: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Deserialize)]
struct OpenAiMessage {
    role: String,
    #[serde(default)]
    content: serde_json::Value,
    #[serde(default)]
    tool_calls: Option<Vec<serde_json::Value>>,
    #[serde(default)]
    tool_call_id: Option<String>,
    #[serde(flatten)]
    extra: serde_json::Map<String, serde_json::Value>,
}

/// Responses API 入口请求（同格式进出，不与 chat 互转）。
#[derive(Debug, Deserialize)]
struct OpenAiResponsesRequest {
    model: String,
    #[serde(deserialize_with = "deserialize_responses_input")]
    input: Vec<serde_json::Value>,
    #[serde(default)]
    instructions: Option<String>,
    #[serde(default)]
    tools: Vec<serde_json::Value>,
    #[serde(default)]
    max_output_tokens: Option<u32>,
    /// Responses 的结构化输出参数：text: { format: {...} }。
    /// 注意：Responses API 没有顶层 response_format（那是 Chat 字段）。
    #[serde(default)]
    text: Option<serde_json::Value>,
    #[serde(default)]
    tool_choice: Option<serde_json::Value>,
    /// #2313 要求 openai-responses 目标显式 store:false。
    #[serde(default)]
    store: Option<bool>,
    /// Responses 的会话续写 ID；不透传 = 客户端丢失历史连续性。
    #[serde(default)]
    previous_response_id: Option<String>,
    /// Responses 的会话标识（conversation 参数）；与 previous_response_id
    /// 是不同的字段，同样需要透传给 Core 检查。
    #[serde(default)]
    conversation: Option<String>,
    #[serde(default)]
    stream: Option<bool>,
    /// 入口未建模的其余顶层字段（Codex 发送 reasoning、include、prompt_cache_key、
    /// parallel_tool_calls 等）。原样透传，由 Core 的请求字段白名单裁决。
    #[serde(flatten)]
    extra: serde_json::Map<String, serde_json::Value>,
}

fn deserialize_responses_input<'de, D>(deserializer: D) -> Result<Vec<serde_json::Value>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    #[derive(Deserialize)]
    #[serde(untagged)]
    enum Input {
        Text(String),
        Items(Vec<serde_json::Value>),
    }
    Ok(match Input::deserialize(deserializer)? {
        Input::Text(text) => vec![serde_json::json!({"role": "user", "content": text})],
        Input::Items(items) => items,
    })
}

pub async fn chat_completions(
    State((state, _)): State<SharedProxy>,
    headers: HeaderMap,
    req: Request,
) -> Response {
    ingress_common(state, headers, req, Format::Chat).await
}

pub async fn responses(
    State((state, _)): State<SharedProxy>,
    headers: HeaderMap,
    req: Request,
) -> Response {
    ingress_common(state, headers, req, Format::Responses).await
}

enum Format {
    Chat,
    Responses,
}

async fn ingress_common(
    state: Arc<AppState>,
    headers: HeaderMap,
    req: Request,
    format: Format,
) -> Response {
    let control = RequestControl::new(
        tokio::time::Instant::now() + std::time::Duration::from_secs(180),
        req.extensions().get::<String>().cloned(),
    );
    let observation = control.observation();
    let cancellation = control.cancellation();
    let mut response = ingress_model(state.clone(), headers, req, format, control).await;
    if matches!(
        observation.snapshot().execution,
        pc_gateway_core::ExecutionState::Finished(
            pc_gateway_core::ExecutionTerminal::ReleaseAuthorized
                | pc_gateway_core::ExecutionTerminal::Degraded
        )
    ) {
        let first_content = response
            .extensions()
            .get::<FirstContentOffset>()
            .and_then(|offset| offset.0);
        let (parts, body) = response.into_parts();
        response = Response::from_parts(
            parts,
            crate::evidence_body::EvidenceBody::wrap(
                body,
                observation.clone(),
                state.evidence.clone(),
                first_content,
            ),
        );
    }
    response.extensions_mut().insert(observation);
    response.extensions_mut().insert(cancellation);
    response
}

async fn ingress_model(
    state: Arc<AppState>,
    headers: HeaderMap,
    req: Request,
    format: Format,
    control: RequestControl,
) -> Response {
    // 网关密钥认证（替代服务商 key；上游凭据由受控配置持有）。
    if let Some(expected) = &state.api_key {
        if !crate::authentication::authenticate(expected, &headers) {
            return (
                StatusCode::UNAUTHORIZED,
                [(header::WWW_AUTHENTICATE, "Bearer")],
                Json(serde_json::json!({ "error": "invalid_gateway_key" })),
            )
                .into_response();
        }
    }

    if state.max_body_bytes == 0 {
        return respond(
            ManagedOutcome::GatePaused("input_capacity_unconfirmed".into()),
            match format {
                Format::Chat => OutputProtocol::OpenAiChat,
                Format::Responses => OutputProtocol::OpenAiResponses,
            },
        );
    }

    // Applies the configured limit to the consumed body as well: chunked requests have no Content-Length,
    // so the middleware precheck cannot limit them. Here we use the same configured value to prevent bypassing via framing.
    let body = match axum::body::to_bytes(req.into_body(), state.max_body_bytes).await {
        Ok(b) => b,
        Err(e) => {
            // 区分超限（413，与中间件预检语义一致）与其他读取错误（400）。
            let over_limit = std::error::Error::source(&e)
                .and_then(|s| s.downcast_ref::<http_body_util::LengthLimitError>())
                .is_some();
            let (status, code) = if over_limit {
                (StatusCode::PAYLOAD_TOO_LARGE, "request_body_too_large")
            } else {
                (StatusCode::BAD_REQUEST, "invalid_body")
            };
            return (
                status,
                Json(serde_json::json!({ "error": code, "detail": e.to_string() })),
            )
                .into_response();
        }
    };
    let mut client_stream = false;
    let canonical = match format {
        Format::Chat => {
            let chat: OpenAiChatRequest = match serde_json::from_slice(&body) {
                Ok(c) => c,
                Err(e) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": "invalid_openai_body", "detail": e.to_string() })),
                    )
                        .into_response()
                }
            };
            match to_canonical_chat(chat) {
                Ok(c) => c,
                Err(msg) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": "unsupported_field", "detail": msg })),
                    )
                        .into_response()
                }
            }
        }
        Format::Responses => {
            let mut r: OpenAiResponsesRequest = match serde_json::from_slice(&body) {
                Ok(c) => c,
                Err(e) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": "invalid_responses_body", "detail": e.to_string() })),
                    )
                        .into_response()
                }
            };
            client_stream = r.stream.unwrap_or(false);
            if let Some(text) = &state.steering {
                crate::steering::insert(&mut r.input, text);
            }
            match to_canonical_responses(r) {
                Ok(c) => c,
                Err(msg) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": "unsupported_field", "detail": msg })),
                    )
                        .into_response()
                }
            }
        }
    };
    // P1 review：未配置的上游禁用路由（不默认到任何第三方）。
    if let Format::Responses = format {
        if state.upstream_responses.is_empty() {
            return (
                StatusCode::NOT_IMPLEMENTED,
                Json(serde_json::json!({
                    "error": "responses_route_disabled",
                    "hint": "set PC_GATEWAY_UPSTREAM_URL_RESPONSES to enable this route"
                })),
            )
                .into_response();
        }
    }
    let renderer = match format {
        Format::Chat => pc_gateway_protocol::GatewayRenderer::Chat(Default::default()),
        Format::Responses => pc_gateway_protocol::GatewayRenderer::Responses(Default::default()),
    };

    let conversation = conversation_id(&headers, &format);
    let call = ManagedCall {
        scope_id: state.default_scope.clone(),
        target_profile: state.upstream_profile.clone(),
        request: canonical,
        target_url: match format {
            Format::Chat => state.upstream_chat.clone(),
            Format::Responses => state.upstream_responses.clone(),
        },
        headers: bearer_headers(match format {
            Format::Chat => &state.upstream_chat_api_key,
            Format::Responses => &state.upstream_responses_api_key,
        }),
        // P1-3：降级资格由启动时 capabilities 查询决定，环境变量只是运维申请。
        degrade_policy: if state.degradation_allowed {
            state.degrade
        } else {
            pc_gateway_core::DegradePolicy::Pause
        },
        inject_context: injects(
            &format,
            state.injection,
            state.inject_responses,
            conversation.is_some(),
        ),
        include_code: state.inject_code,
        conversation_id: conversation,
        sediment: state.sediment,
        prepared_contexts: Vec::new(),
    };

    let outcome = state
        .executor
        .run(control, call, &renderer, state.evidence.as_ref())
        .await;
    if client_stream {
        return respond_responses_sse(outcome);
    }
    respond(
        outcome,
        match format {
            Format::Chat => OutputProtocol::OpenAiChat,
            Format::Responses => OutputProtocol::OpenAiResponses,
        },
    )
}

/// 流式 Responses 客户端：把放行（或允许降级直通）后的完整响应重放为 Responses SSE 事件。
/// 非 200 或无法解析的结果按普通 JSON 返回，错误状态码不被包装成流；
/// 降级直通的流保留 `x-pc-degraded` 头。
fn respond_responses_sse(outcome: ManagedOutcome) -> Response {
    let (body, degraded) = match &outcome {
        ManagedOutcome::Released {
            status: 200, body, ..
        } => (body, false),
        ManagedOutcome::Degraded {
            status: 200, body, ..
        } => (body, true),
        _ => return respond(outcome, OutputProtocol::OpenAiResponses),
    };
    let Ok(resp) = serde_json::from_slice::<serde_json::Value>(body) else {
        return respond(outcome, OutputProtocol::OpenAiResponses);
    };
    let body = responses_sse(&resp);
    let first_content =
        first_model_content_offset(body.as_bytes(), OutputProtocol::OpenAiResponses, true);
    let mut response = (
        StatusCode::OK,
        [(header::CONTENT_TYPE, "text/event-stream")],
        body,
    )
        .into_response();
    response
        .extensions_mut()
        .insert(FirstContentOffset(first_content));
    if degraded {
        response.headers_mut().insert(
            "x-pc-degraded",
            header::HeaderValue::from_static("passthrough_without_memory"),
        );
    }
    response
}

/// 事件序列：response.created → 每个 output item 的 output_item.added/done
/// （message 的每个内容部件在其间重放 content_part.added → 一次性 delta → done → content_part.done）
/// → 按响应状态选择的终止事件（completed / incomplete / failed）。
fn responses_sse(resp: &serde_json::Value) -> String {
    use serde_json::{json, Value};
    let mut out = String::new();
    let mut seq: u64 = 0;
    let mut push = |event: &str, mut data: Value| {
        data["type"] = json!(event);
        data["sequence_number"] = json!(seq);
        seq += 1;
        out.push_str("event: ");
        out.push_str(event);
        out.push_str("\ndata: ");
        out.push_str(&data.to_string());
        out.push_str("\n\n");
    };
    let mut created = resp.clone();
    created["status"] = json!("in_progress");
    created["output"] = json!([]);
    if created.get("error").is_some() {
        created["error"] = Value::Null;
    }
    push("response.created", json!({"response": created}));
    let items = resp.get("output").and_then(Value::as_array);
    for (output_index, item) in items.into_iter().flatten().enumerate() {
        if item.get("type").and_then(Value::as_str) == Some("reasoning") {
            let mut added = item.clone();
            added["summary"] = json!([]);
            push(
                "response.output_item.added",
                json!({"output_index": output_index, "item": added}),
            );
            let item_id = item.get("id").cloned().unwrap_or(Value::Null);
            for (summary_index, part) in item
                .get("summary")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .enumerate()
            {
                let text = part.get("text").cloned().unwrap_or(json!(""));
                let mut empty = part.clone();
                empty["text"] = json!("");
                push(
                    "response.reasoning_summary_part.added",
                    json!({
                        "item_id": item_id, "output_index": output_index, "summary_index": summary_index, "part": empty,
                    }),
                );
                push(
                    "response.reasoning_summary_text.delta",
                    json!({
                        "item_id": item_id, "output_index": output_index, "summary_index": summary_index, "delta": text,
                    }),
                );
                push(
                    "response.reasoning_summary_text.done",
                    json!({
                        "item_id": item_id, "output_index": output_index, "summary_index": summary_index, "text": text,
                    }),
                );
                push(
                    "response.reasoning_summary_part.done",
                    json!({
                        "item_id": item_id, "output_index": output_index, "summary_index": summary_index, "part": part,
                    }),
                );
            }
            push(
                "response.output_item.done",
                json!({"output_index": output_index, "item": item}),
            );
            continue;
        }
        let parts = match (
            item.get("type").and_then(Value::as_str),
            item.get("content"),
        ) {
            (Some("message"), Some(Value::Array(parts))) => Some(parts),
            _ => None,
        };
        let Some(parts) = parts else {
            let function_call = item.get("type").and_then(Value::as_str) == Some("function_call");
            let mut added = item.clone();
            if function_call {
                added["arguments"] = json!("");
                added["status"] = json!("in_progress");
            }
            push(
                "response.output_item.added",
                json!({"output_index": output_index, "item": added}),
            );
            if function_call {
                let item_id = item.get("id").cloned().unwrap_or(Value::Null);
                let arguments = item.get("arguments").cloned().unwrap_or(json!(""));
                push(
                    "response.function_call_arguments.delta",
                    json!({"item_id": item_id, "output_index": output_index, "delta": arguments}),
                );
                push(
                    "response.function_call_arguments.done",
                    json!({
                        "item_id": item_id, "output_index": output_index,
                        "arguments": arguments, "name": item.get("name").cloned().unwrap_or(Value::Null),
                    }),
                );
            }
            push(
                "response.output_item.done",
                json!({"output_index": output_index, "item": item}),
            );
            continue;
        };
        // 与上游流一致：added 时 message 尚无内容，内容由随后的部件事件补齐。
        let mut added = item.clone();
        added["content"] = json!([]);
        added["status"] = json!("in_progress");
        push(
            "response.output_item.added",
            json!({"output_index": output_index, "item": added}),
        );
        let item_id = item.get("id").cloned().unwrap_or(Value::Null);
        for (content_index, part) in parts.iter().enumerate() {
            let at = json!({"item_id": item_id, "output_index": output_index, "content_index": content_index});
            let with = |mut base: Value, extra: Value| {
                if let (Some(b), Some(e)) = (base.as_object_mut(), extra.as_object()) {
                    b.extend(e.clone());
                }
                base
            };
            let mut empty = part.clone();
            let text_kind = match part.get("type").and_then(Value::as_str) {
                Some("output_text") => {
                    empty["text"] = json!("");
                    if empty.get("annotations").is_some() {
                        empty["annotations"] = json!([]);
                    }
                    if empty.get("logprobs").is_some() {
                        empty["logprobs"] = json!([]);
                    }
                    Some(("text", "response.output_text"))
                }
                Some("refusal") => {
                    empty["refusal"] = json!("");
                    Some(("refusal", "response.refusal"))
                }
                _ => None,
            };
            push(
                "response.content_part.added",
                with(at.clone(), json!({"part": empty})),
            );
            if let Some((field, prefix)) = text_kind {
                let text = part.get(field).cloned().unwrap_or(json!(""));
                let is_text = field == "text";
                let mut delta = with(at.clone(), json!({"delta": text}));
                let mut done = with(at.clone(), json!({ field: text }));
                if is_text {
                    let logprobs = json!({
                        "logprobs": part.get("logprobs").filter(|value| !value.is_null()).cloned().unwrap_or(json!([]))
                    });
                    delta = with(delta, logprobs.clone());
                    done = with(done, logprobs);
                }
                push(&format!("{prefix}.delta"), delta);
                if is_text {
                    for (annotation_index, annotation) in part
                        .get("annotations")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                        .enumerate()
                    {
                        push(
                            "response.output_text.annotation.added",
                            with(
                                at.clone(),
                                json!({
                                    "annotation_index": annotation_index, "annotation": annotation,
                                }),
                            ),
                        );
                    }
                }
                push(&format!("{prefix}.done"), done);
            }
            push(
                "response.content_part.done",
                with(at, json!({"part": part})),
            );
        }
        push(
            "response.output_item.done",
            json!({"output_index": output_index, "item": item}),
        );
    }
    // 终止事件取自已获准的响应状态：失败或不完整的响应不能被报告为完成。
    let has_error = resp.get("error").is_some_and(|e| !e.is_null());
    let terminal = match resp.get("status").and_then(Value::as_str) {
        _ if has_error => "response.failed",
        Some("failed") | Some("cancelled") => "response.failed",
        Some("incomplete") => "response.incomplete",
        _ => "response.completed",
    };
    push(terminal, json!({"response": resp}));
    out
}

/// `PC_GATEWAY_INJECTION` 决定注入范围（默认只注入带会话 ID 的请求）并作用于两个入口；`PC_GATEWAY_INJECTION_RESPONSES=off` 只关闭 Responses。
fn injects(format: &Format, injection: Injection, responses: bool, has_conversation: bool) -> bool {
    let enabled = match injection {
        Injection::All => true,
        Injection::ConversationsOnly => has_conversation,
        Injection::Off => false,
    };
    enabled && (matches!(format, Format::Chat) || responses)
}

/// 请求所属的会话，决定准备结果在哪些请求之间重放。依次取：
/// - 宿主提供的 `X-PowerContext-Conversation-ID`（RFC 2199），优先于各客户端的原生头；
/// - Claude Code 的 `x-claude-code-session-id`：主代理与子代理共享它，子代理的请求另带
///   `x-claude-code-agent-id`，所以会话是二者的组合；
/// - OpenCode 的 `x-opencode-session-id`：子会话有自己的值。`x-opencode-session`、
///   `x-session-affinity` 等亲和头可能让子会话沿用父会话的值，不用作身份；
/// - Responses 入口上，Codex 的 `thread-id`：一个线程的每个请求同值，子代理线程的值不同。它们共享的
///   `session-id` 和 `prompt_cache_key` 不用作身份。`thread-id` 不带客户端前缀，所以只在 Responses
///   入口识别。
///
/// 各来源加不同前缀，互不相同。值须为 1～256 个可见 ASCII 字符；都没有时按没有会话处理：
/// 不在请求之间重放准备结果，请求照常处理。
fn conversation_id(headers: &HeaderMap, format: &Format) -> Option<String> {
    if let Some(id) = header_id(headers, CONVERSATION_ID_HEADER) {
        return Some(format!("powercontext:{id}"));
    }
    if let Some(session) = header_id(headers, CLAUDE_CODE_SESSION_HEADER) {
        // 合法值不含空格，用空格分隔会话与代理，组合不会与另一组值重合。
        return Some(match header_id(headers, CLAUDE_CODE_AGENT_HEADER) {
            Some(agent) => format!("claude-code:{session} {agent}"),
            None => format!("claude-code:{session}"),
        });
    }
    if let Some(session) = header_id(headers, OPENCODE_SESSION_HEADER) {
        return Some(format!("opencode:{session}"));
    }
    match format {
        Format::Responses => {
            header_id(headers, CODEX_THREAD_ID_HEADER).map(|id| format!("codex-thread:{id}"))
        }
        Format::Chat => None,
    }
}

fn header_id<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    let value = headers.get(name)?.to_str().ok()?;
    let valid =
        !value.is_empty() && value.len() <= 256 && value.bytes().all(|b| b.is_ascii_graphic());
    valid.then_some(value)
}

const CONVERSATION_ID_HEADER: &str = "x-powercontext-conversation-id";
const CLAUDE_CODE_SESSION_HEADER: &str = "x-claude-code-session-id";
const CLAUDE_CODE_AGENT_HEADER: &str = "x-claude-code-agent-id";
const OPENCODE_SESSION_HEADER: &str = "x-opencode-session-id";
const CODEX_THREAD_ID_HEADER: &str = "thread-id";

fn bearer_headers(key: &str) -> Vec<(String, String)> {
    if key.is_empty() {
        Vec::new()
    } else {
        vec![("authorization".to_string(), format!("Bearer {key}"))]
    }
}

fn to_canonical_chat(chat: OpenAiChatRequest) -> Result<CanonicalRequest, String> {
    let mut messages = Vec::with_capacity(chat.messages.len());
    for m in chat.messages {
        let role = match m.role.as_str() {
            "system" => Role::System,
            "developer" => Role::Developer,
            "user" => Role::User,
            "assistant" => Role::Assistant,
            "tool" => Role::Tool,
            other => return Err(format!("unsupported role: {other}")),
        };
        let content_null = m.content.is_null();
        let content = match m.content {
            serde_json::Value::String(s) => vec![serde_json::Value::String(s)],
            serde_json::Value::Array(a) => a,
            serde_json::Value::Null => vec![],
            other => return Err(format!("unsupported content type: {other}")),
        };
        messages.push(CanonicalMessage {
            role,
            content,
            content_null,
            tool_calls: m.tool_calls,
            extra_fields: m.extra,
            tool_call_id: m.tool_call_id,
        });
    }
    Ok(CanonicalRequest {
        model: chat.model,
        messages,
        tools: chat.tools,
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: chat.max_completion_tokens.or(chat.max_tokens),
        },
        stream: chat.stream.unwrap_or(false),
        response_format: chat.response_format,
        tool_choice: chat.tool_choice,
        store: None,
        previous_response_id: None,
        passthrough_fields: (!chat.extra.is_empty()).then_some(chat.extra),
        token_limit_field: if chat.max_completion_tokens.is_some() {
            Some("max_completion_tokens".into())
        } else if chat.max_tokens.is_some() {
            Some("max_tokens".into())
        } else {
            None
        },
        conversation: None,
    })
}

/// Responses 入口 → 规范类型。
///
/// 只有普通用户消息进入规范形态（注入与召回查询只需要它们）。其余 input item
/// 一律原样封装为单元素 content，由 `OpenAiResponsesRenderer` 原样作为顶层 item 透出：
/// developer/system 消息保留原 role 与 type，function_call 等专有 item、以及
/// Codex 放工具定义的 `additional_tools` 这类不在白名单里的 item 也不被改写。
fn to_canonical_responses(r: OpenAiResponsesRequest) -> Result<CanonicalRequest, String> {
    let mut messages = Vec::with_capacity(r.input.len());
    for item in r.input {
        let role = item.get("role").and_then(|v| v.as_str()).unwrap_or("");
        let kind = item
            .get("type")
            .and_then(|v| v.as_str())
            .unwrap_or("message");
        if role != "user" || kind != "message" {
            messages.push(CanonicalMessage {
                role: Role::Tool,
                content: vec![item],
                content_null: false,
                tool_calls: None,
                extra_fields: Default::default(),
                tool_call_id: None,
            });
            continue;
        }
        let content = match item.get("content") {
            Some(serde_json::Value::String(s)) => vec![serde_json::Value::String(s.clone())],
            Some(serde_json::Value::Array(a)) => a.clone(),
            _ => vec![],
        };
        messages.push(CanonicalMessage {
            role: Role::User,
            content,
            content_null: false,
            tool_calls: None,
            extra_fields: Default::default(),
            tool_call_id: None,
        });
    }
    let mut extra = r.extra;
    // client_metadata 是客户端本地的回合元数据（Codex 的 x-codex-turn-metadata），不是发给模型的字段。
    extra.remove("client_metadata");
    Ok(CanonicalRequest {
        model: r.model,
        messages,
        tools: r.tools,
        instructions: r.instructions,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: r.max_output_tokens,
        },
        // 上游一律非流式：发送检查目前只支持 openai-chat 的流式，Responses 流式请求会被 withhold。
        // 客户端要求流式时，由 `respond_responses_sse` 在 before_release 放行后重放为 SSE，
        // 保持“检查通过前不释放任何内容”的语义。
        stream: false,
        // Responses 的结构化输出在 text.format，不在 response_format（Chat 字段）。
        response_format: r.text,
        tool_choice: r.tool_choice,
        store: r.store,
        previous_response_id: r.previous_response_id,
        conversation: r.conversation,
        token_limit_field: None,
        passthrough_fields: (!extra.is_empty()).then_some(extra),
    })
}

/// 结果映射：不存在静默成功；降级直通必须显式标记。
fn respond(outcome: ManagedOutcome, protocol: OutputProtocol) -> Response {
    let first_content = match &outcome {
        ManagedOutcome::Released {
            status,
            content_type,
            body,
            ..
        }
        | ManagedOutcome::Degraded {
            status,
            content_type,
            body,
        } if (200..300).contains(status) => {
            let mime = content_type.split(';').next().unwrap_or("").trim();
            if mime.eq_ignore_ascii_case("application/json")
                || mime.eq_ignore_ascii_case("text/event-stream")
            {
                first_model_content_offset(
                    body,
                    protocol,
                    mime.eq_ignore_ascii_case("text/event-stream"),
                )
            } else {
                None
            }
        }
        _ => None,
    };
    let mut response = match outcome {
        ManagedOutcome::Released {
            status,
            content_type,
            body,
            ..
        } => {
            // 上游状态码与 Content-Type 原样透传（错误不伪装 200）。
            let code = StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY);
            (
                code,
                [(header::CONTENT_TYPE, content_type.as_str())],
                body.to_vec(),
            )
                .into_response()
        }
        ManagedOutcome::Degraded {
            status,
            content_type,
            body,
        } => match Response::builder()
            .status(StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY))
            .header(header::CONTENT_TYPE, content_type.as_str())
            .header("x-pc-degraded", "passthrough_without_memory")
            .body(axum::body::Body::from(body.to_vec()))
        {
            Ok(r) => r,
            Err(e) => (
                StatusCode::INTERNAL_SERVER_ERROR,
                axum::Json(serde_json::json!({ "error": e.to_string() })),
            )
                .into_response(),
        },
        ManagedOutcome::Blocked { reason_codes } => (
            StatusCode::FORBIDDEN,
            Json(serde_json::json!({ "decision": "block", "reason_codes": reason_codes })),
        )
            .into_response(),
        ManagedOutcome::Withheld { reason_codes } => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({ "decision": "withhold", "reason_codes": reason_codes })),
        )
            .into_response(),
        ManagedOutcome::SendIndeterminate => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "decision": "send_indeterminate" })),
        )
            .into_response(),
        ManagedOutcome::NotSent { attempts } => (
            StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "decision": "not_sent", "attempts": attempts })),
        )
            .into_response(),
        ManagedOutcome::AdmissionExpired => (
            StatusCode::CONFLICT,
            Json(serde_json::json!({ "decision": "admission_expired" })),
        )
            .into_response(),
        ManagedOutcome::GatePaused(msg) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({ "decision": "gate_paused", "detail": msg })),
        )
            .into_response(),
    };
    response
        .extensions_mut()
        .insert(FirstContentOffset(first_content));
    response
}

// 抑制未使用导入警告（Bytes 用于类型别名文档场景）。
#[allow(dead_code)]
fn _unused(_: Option<Bytes>) {}

#[cfg(test)]
mod tests {
    //! PR #2307 review P2：chat 工具元数据经入口 canonical 化的保真测试。
    use super::*;

    async fn lifecycle_router() -> (axum::Router, tokio::task::JoinHandle<()>) {
        lifecycle_router_with_evidence(Arc::new(pc_gateway_evidence::NoopEvidenceSink)).await
    }

    async fn lifecycle_router_with_evidence(
        evidence: Arc<dyn pc_gateway_evidence::EvidenceSink>,
    ) -> (axum::Router, tokio::task::JoinHandle<()>) {
        use axum::routing::post;
        use pc_gateway_core::{Executor, HttpGateClient};
        use pc_gateway_types::{CheckPhase, ContextCheckRequest};
        let mock = axum::Router::new()
            .route(
                "/v1/context/check",
                post(|Json(request): Json<ContextCheckRequest>| async move {
                    let phase = match request {
                        ContextCheckRequest::BeforeSend { .. } => CheckPhase::BeforeSend,
                        ContextCheckRequest::BeforeRelease { .. } => CheckPhase::BeforeRelease,
                    };
                    Json(serde_json::json!({
                        "schema": pc_gateway_types::CONTEXT_CHECK_SCHEMA,
                        "phase": phase,
                        "decision": "allow",
                        "reason_codes": [],
                        "coverage": {"profile": "text-and-tools.v1", "complete": true},
                        "policy_version": "test",
                        "valid_until": "2999-01-01T00:00:00Z",
                        "binding": "test-binding"
                    }))
                }),
            )
            .route(
                "/v1/chat/completions",
                post(|Json(request): Json<serde_json::Value>| async move {
                    let usage = serde_json::json!({"prompt_tokens": 100, "completion_tokens": 12, "prompt_tokens_details": {"cached_tokens": 70}});
                    if request["stream"] == true {
                        (
                            StatusCode::CREATED,
                            [(header::CONTENT_TYPE, "text/event-stream")],
                            format!("data: {{\"choices\":[{{\"delta\":{{\"role\":\"assistant\"}}}}]}}\n\ndata: {{\"choices\":[{{\"delta\":{{\"content\":\"SYNTHETIC_SENSITIVE_RESPONSE\"}}}}]}}\n\ndata: {{\"choices\":[],\"usage\":{usage}}}\n\ndata: [DONE]\n\n"),
                        ).into_response()
                    } else {
                        (
                            StatusCode::CREATED,
                            Json(serde_json::json!({
                                "choices": [{"message": {"role": "assistant", "content": "checked"}}],
                                "usage": usage,
                                "provider_debug": "SYNTHETIC_SENSITIVE_RESPONSE"
                            })),
                        ).into_response()
                    }
                }),
            )
            .route(
                "/v1/responses",
                post(|| async {
                    Json(serde_json::json!({
                        "id": "resp-1", "object": "response", "status": "completed", "output": [{"type":"message", "role":"assistant", "content":[{"type":"output_text", "text":"SYNTHETIC_SENSITIVE_RESPONSE"}]}],
                        "usage": {"input_tokens": 80, "output_tokens": 11, "input_tokens_details": {"cached_tokens": 30}}
                    }))
                }),
            );
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let server = tokio::spawn(async move { axum::serve(listener, mock).await.unwrap() });
        let state = Arc::new(AppState {
            executor: Arc::new(Executor::new(
                crate::link::CoreLink::Http(HttpGateClient::with_token(&base, None)),
                pc_gateway_transport::ReqwestTransport::new(std::time::Duration::from_secs(120)),
                std::num::NonZeroU32::new(2).unwrap(),
            )),
            evidence,
            upstream_profile: "test".into(),
            upstream_chat: format!("{base}/v1/chat/completions"),
            upstream_responses: format!("{base}/v1/responses"),
            upstream_chat_api_key: "SYNTHETIC_SENSITIVE_CREDENTIAL".into(),
            upstream_responses_api_key: String::new(),
            api_key: Some("gateway-key".into()),
            degrade: pc_gateway_core::DegradePolicy::Pause,
            injection: Injection::Off,
            inject_responses: false,
            inject_code: false,
            sediment: false,
            steering: None,
            default_scope: "test".into(),
            max_body_bytes: 1024 * 1024,
            degradation_allowed: false,
        });
        let router = crate::middleware::apply_chain(
            crate::router(state, crate::proxy::ProxyState::from_env()),
            vec![
                crate::middleware::ResourceLimits::new(1024 * 1024, 1024 * 1024, 2),
                Arc::new(crate::middleware::RequestId),
            ],
        );
        (router, server)
    }

    struct PendingBody {
        receiver: tokio::sync::oneshot::Receiver<Bytes>,
        done: bool,
    }

    impl http_body::Body for PendingBody {
        type Data = Bytes;
        type Error = std::convert::Infallible;

        fn poll_frame(
            mut self: std::pin::Pin<&mut Self>,
            context: &mut std::task::Context<'_>,
        ) -> std::task::Poll<Option<Result<http_body::Frame<Bytes>, Self::Error>>> {
            use std::future::Future;
            if self.done {
                return std::task::Poll::Ready(None);
            }
            match std::pin::Pin::new(&mut self.receiver).poll(context) {
                std::task::Poll::Pending => std::task::Poll::Pending,
                std::task::Poll::Ready(Ok(bytes)) => {
                    self.done = true;
                    std::task::Poll::Ready(Some(Ok(http_body::Frame::data(bytes))))
                }
                std::task::Poll::Ready(Err(_)) => std::task::Poll::Ready(None),
            }
        }
    }

    #[tokio::test(start_paused = true)]
    async fn model_routes_create_control_before_reading_the_body_and_retain_it_after_execution() {
        use pc_gateway_core::{
            ExecutionObservation, ExecutionState, ExecutionTerminal, ModelSendState,
            RequestCancellation,
        };
        use std::future::Future;
        use tower::ServiceExt;
        for (path, body, streaming) in [
            (
                "/gateway/v1/chat/completions",
                serde_json::json!({"model": "test", "messages": [{"role": "user", "content": "hello"}]}),
                false,
            ),
            (
                "/gateway/v1/responses",
                serde_json::json!({"model": "test", "input": [{"role": "user", "content": "hello"}], "store": false}),
                false,
            ),
            (
                "/gateway/v1/responses",
                serde_json::json!({"model": "test", "input": [{"role": "user", "content": "hello"}], "store": false, "stream": true}),
                true,
            ),
        ] {
            let (router, server) = lifecycle_router().await;
            let (sender, receiver) = tokio::sync::oneshot::channel();
            let started = tokio::time::Instant::now();
            let request = Request::builder()
                .method("POST")
                .uri(path)
                .header(header::AUTHORIZATION, "Bearer gateway-key")
                .header("x-powercontext-request-id", "route-request")
                .body(axum::body::Body::new(PendingBody {
                    receiver,
                    done: false,
                }))
                .unwrap();
            let mut response = Box::pin(router.oneshot(request));
            assert!(std::future::poll_fn(|context| std::task::Poll::Ready(
                response.as_mut().poll(context)
            ))
            .await
            .is_pending());
            tokio::time::advance(std::time::Duration::from_secs(11)).await;
            tokio::time::resume();
            sender
                .send(serde_json::to_vec(&body).unwrap().into())
                .unwrap();
            let response = response.await.unwrap();
            let observation = response
                .extensions()
                .get::<ExecutionObservation>()
                .unwrap()
                .clone();
            let cancellation = response
                .extensions()
                .get::<RequestCancellation>()
                .unwrap()
                .clone();
            assert_eq!(
                observation.deadline(),
                started + std::time::Duration::from_secs(180)
            );
            assert_eq!(observation.request_id(), Some("route-request"));
            assert_eq!(
                observation.snapshot().model_send,
                ModelSendState::ResponseReceived
            );
            assert_eq!(
                observation.snapshot().execution,
                ExecutionState::Finished(ExecutionTerminal::ReleaseAuthorized)
            );
            assert_eq!(
                response.headers()["x-powercontext-request-id"],
                "route-request"
            );
            assert_eq!(
                response.status(),
                if path.contains("chat") {
                    StatusCode::CREATED
                } else {
                    StatusCode::OK
                }
            );
            assert_eq!(
                response.headers()[header::CONTENT_TYPE],
                if streaming {
                    "text/event-stream"
                } else {
                    "application/json"
                }
            );
            let bytes = axum::body::to_bytes(response.into_body(), 1024 * 1024)
                .await
                .unwrap();
            if streaming {
                let text = std::str::from_utf8(&bytes).unwrap();
                assert!(text.contains("event: response.created"));
                assert!(text.contains("event: response.completed"));
            } else {
                let content: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
                if path.contains("chat") {
                    assert_eq!(content["choices"][0]["message"]["content"], "checked");
                } else {
                    assert_eq!(content["id"], "resp-1");
                }
            }
            let terminal = observation.snapshot();
            cancellation.cancel();
            drop(cancellation);
            assert_eq!(observation.snapshot(), terminal);
            server.abort();
            tokio::time::pause();
        }
    }

    #[tokio::test]
    async fn retained_lifecycle_handles_survive_an_unconsumed_response_drop() {
        use pc_gateway_core::{
            ExecutionObservation, ExecutionState, ExecutionTerminal, ModelSendState,
            RequestCancellation,
        };
        use tower::ServiceExt;

        for (path, body) in [
            (
                "/gateway/v1/chat/completions",
                serde_json::json!({"model": "test", "messages": [{"role": "user", "content": "hello"}]}),
            ),
            (
                "/gateway/v1/responses",
                serde_json::json!({"model": "test", "input": [{"role": "user", "content": "hello"}], "store": false}),
            ),
            (
                "/gateway/v1/responses",
                serde_json::json!({"model": "test", "input": [{"role": "user", "content": "hello"}], "store": false, "stream": true}),
            ),
        ] {
            let (router, server) = lifecycle_router().await;
            let response = router
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri(path)
                        .header(header::AUTHORIZATION, "Bearer gateway-key")
                        .header("x-powercontext-request-id", "dropped-response")
                        .body(axum::body::Body::from(serde_json::to_vec(&body).unwrap()))
                        .unwrap(),
                )
                .await
                .unwrap();
            let observation = response
                .extensions()
                .get::<ExecutionObservation>()
                .unwrap()
                .clone();
            let cancellation = response
                .extensions()
                .get::<RequestCancellation>()
                .unwrap()
                .clone();
            let deadline = observation.deadline();
            let snapshot = observation.snapshot();
            assert_eq!(snapshot.model_send, ModelSendState::ResponseReceived);
            assert_eq!(
                snapshot.execution,
                ExecutionState::Finished(ExecutionTerminal::ReleaseAuthorized)
            );

            drop(response);

            assert_eq!(observation.request_id(), Some("dropped-response"));
            assert_eq!(observation.deadline(), deadline);
            assert_eq!(observation.snapshot(), snapshot);
            cancellation.cancel();
            assert!(cancellation.is_cancelled());
            drop(cancellation);
            assert_eq!(observation.snapshot(), snapshot);
            server.abort();
        }
    }

    #[test]
    fn chat_ingress_preserves_tool_fields_and_null_content() {
        let body = r#"{
            "model": "m",
            "messages": [
                {"role": "assistant", "content": null,
                 "tool_calls": [{"id": "call_1", "type": "function",
                                  "function": {"name": "get_weather", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}
            ],
            "max_tokens": 8
        }"#;
        let chat: OpenAiChatRequest = serde_json::from_str(body).unwrap();
        let canonical = to_canonical_chat(chat).unwrap();
        let a = &canonical.messages[0];
        assert!(a.content_null, "content:null 必须被记录");
        assert!(a.content.is_empty());
        assert_eq!(a.tool_calls.as_deref().map(|t| t.len()), Some(1));
        let t = &canonical.messages[1];
        assert_eq!(t.tool_call_id.as_deref(), Some("call_1"));
        assert_eq!(t.content[0], serde_json::json!("sunny"));
    }

    /// 按 Codex 0.145 的真实请求形态构造（工具定义在 additional_tools item 里）。
    fn codex_request(stream: bool) -> serde_json::Value {
        serde_json::json!({
            "model": "gpt-5.6-sol",
            "input": [
                {"type": "additional_tools", "role": "developer",
                 "tools": [{"type": "custom", "name": "exec", "description": "Run code"}]},
                {"type": "message", "role": "developer",
                 "content": [{"type": "input_text", "text": "base instructions"}]},
                {"role": "developer", "content": "shorthand developer message"},
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "Fix the bug"}]},
                {"type": "reasoning", "summary": [], "encrypted_content": "gAAAA"},
                {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": "ls"},
                {"type": "custom_tool_call_output", "call_id": "c1", "output": "util.py"}
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": false,
            "reasoning": {"effort": "low", "context": "all_turns"},
            "store": false,
            "stream": stream,
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": "session-1",
            "client_metadata": {"x-codex-turn-metadata": "{}"}
        })
    }

    fn render_responses(client: &serde_json::Value) -> serde_json::Value {
        use pc_gateway_protocol::WireRenderer;
        let r: OpenAiResponsesRequest = serde_json::from_value(client.clone()).unwrap();
        let canonical = to_canonical_responses(r).unwrap();
        let payload = pc_gateway_protocol::OpenAiResponsesRenderer
            .render(&canonical, None)
            .unwrap();
        serde_json::from_slice(&payload.bytes).unwrap()
    }

    #[test]
    fn steering_is_rendered_after_the_base_prompt_and_nothing_else_changes() {
        let client = codex_request(false);
        let mut steered = client.clone();
        let mut input: Vec<serde_json::Value> =
            serde_json::from_value(steered["input"].take()).unwrap();
        crate::steering::insert(&mut input, "Plan before acting.");
        steered["input"] = input.into();
        let upstream = render_responses(&steered);
        let plain = render_responses(&client);
        let sent = upstream["input"].as_array().unwrap();
        let before = plain["input"].as_array().unwrap();
        // 基础提示后面是 Codex 的简写 developer 消息（字符串内容），所以插入一条新的 developer 消息。
        assert_eq!(
            sent[2],
            serde_json::json!({"type": "message", "role": "developer",
                "content": [{"type": "input_text", "text": "Plan before acting."}]})
        );
        assert_eq!(sent[..2], before[..2]);
        assert_eq!(sent[3..], before[2..]);
        let mut rest = upstream.clone();
        rest.as_object_mut().unwrap().remove("input");
        let mut plain_rest = plain.clone();
        plain_rest.as_object_mut().unwrap().remove("input");
        assert_eq!(rest, plain_rest);
    }

    #[test]
    fn responses_ingress_forwards_non_user_items_verbatim() {
        let client = codex_request(false);
        let upstream = render_responses(&client);
        let sent = client["input"].as_array().unwrap();
        let got = upstream["input"].as_array().unwrap();
        assert_eq!(got.len(), sent.len());
        for (i, (s, g)) in sent.iter().zip(got).enumerate() {
            if s["role"] == "user" {
                // 用户消息走规范形态（供注入使用），内容部件不变。
                assert_eq!(g["role"], "user");
                assert_eq!(g["content"], s["content"]);
            } else {
                assert_eq!(g, s, "input[{i}] must reach the upstream unchanged");
            }
        }
    }

    #[test]
    fn responses_ingress_passes_through_unmodeled_fields() {
        let upstream = render_responses(&codex_request(false));
        for field in [
            "reasoning",
            "include",
            "prompt_cache_key",
            "parallel_tool_calls",
        ] {
            assert_eq!(
                upstream[field],
                codex_request(false)[field],
                "{field} must be forwarded"
            );
        }
        assert!(
            upstream.get("client_metadata").is_none(),
            "client-local metadata is not sent"
        );
        assert_eq!(upstream["tool_choice"], "auto");
        assert_eq!(upstream["store"], false);
    }

    #[test]
    fn passthrough_fields_never_override_modeled_ones() {
        let r: OpenAiResponsesRequest = serde_json::from_value(serde_json::json!({
            "model": "m", "input": [], "store": false, "metadata": {"k": "v"}
        }))
        .unwrap();
        let canonical = to_canonical_responses(r).unwrap();
        let extra = canonical.passthrough_fields.clone().unwrap();
        assert!(extra.contains_key("metadata"));
        assert!(
            !extra.contains_key("store"),
            "modeled fields are not collected twice"
        );
    }

    #[test]
    fn streaming_responses_clients_get_a_non_streaming_upstream_request() {
        let upstream = render_responses(&codex_request(true));
        assert_eq!(upstream["stream"], false);
    }

    fn sse_events(text: &str) -> Vec<serde_json::Value> {
        text.split("\n\n")
            .filter(|e| !e.is_empty())
            .map(|e| {
                let (head, data) = e.split_once("\ndata: ").unwrap();
                let event: serde_json::Value = serde_json::from_str(data).unwrap();
                assert_eq!(head.strip_prefix("event: "), event["type"].as_str());
                event
            })
            .collect()
    }

    fn event_types(events: &[serde_json::Value]) -> Vec<&str> {
        events.iter().map(|e| e["type"].as_str().unwrap()).collect()
    }

    #[test]
    fn released_responses_are_replayed_as_sse_events() {
        let resp = serde_json::json!({
            "id": "resp_1", "object": "response", "status": "completed", "model": "m",
            "output": [
                {"type": "reasoning", "summary": []},
                {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "fixture-ok", "annotations": []}]}
            ],
            "usage": {"input_tokens": 3, "output_tokens": 1}
        });
        let events = sse_events(&responses_sse(&resp));
        assert_eq!(
            event_types(&events),
            [
                "response.created",
                "response.output_item.added",
                "response.output_item.done",
                "response.output_item.added",
                "response.content_part.added",
                "response.output_text.delta",
                "response.output_text.done",
                "response.content_part.done",
                "response.output_item.done",
                "response.completed"
            ]
        );
        assert_eq!(events[0]["response"]["status"], "in_progress");
        assert_eq!(events[3]["item"]["content"], serde_json::json!([]));
        assert_eq!(events[8]["item"], resp["output"][1]);
        assert_eq!(events[9]["response"], resp);
        for (i, e) in events.iter().enumerate() {
            assert_eq!(e["sequence_number"], i);
        }
    }

    #[test]
    fn replayed_text_reaches_delta_stream_consumers() {
        let resp = serde_json::json!({
            "id": "resp_1", "object": "response", "status": "completed",
            "output": [
                {"id": "msg_1", "type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": "fixture-", "annotations": []},
                             {"type": "refusal", "refusal": "no"},
                             {"type": "output_text", "text": "ok", "annotations": []}]}
            ]
        });
        let events = sse_events(&responses_sse(&resp));
        // 只读 output_text.delta 的标准消费循环必须拿到完整答案。
        let text: String = events
            .iter()
            .filter(|e| e["type"] == "response.output_text.delta")
            .map(|e| e["delta"].as_str().unwrap())
            .collect();
        assert_eq!(text, "fixture-ok");
        let refusal = events
            .iter()
            .find(|e| e["type"] == "response.refusal.delta")
            .unwrap();
        assert_eq!(refusal["delta"], "no");
        assert_eq!(refusal["content_index"], 1);
        let done = events
            .iter()
            .find(|e| e["type"] == "response.output_text.done" && e["content_index"] == 2)
            .unwrap();
        assert_eq!(done["text"], "ok");
        assert_eq!(done["item_id"], "msg_1");
    }

    #[test]
    fn replay_terminal_event_follows_response_status() {
        let failed = serde_json::json!({
            "id": "resp_1", "object": "response", "status": "failed", "output": [],
            "error": {"code": "server_error", "message": "upstream failed"}
        });
        let events = sse_events(&responses_sse(&failed));
        assert_eq!(
            event_types(&events),
            ["response.created", "response.failed"]
        );
        assert_eq!(events[1]["response"]["error"]["code"], "server_error");
        assert!(events[0]["response"]["error"].is_null());

        let incomplete = serde_json::json!({
            "id": "resp_2", "object": "response", "status": "incomplete", "output": [],
            "incomplete_details": {"reason": "max_output_tokens"}
        });
        let events = sse_events(&responses_sse(&incomplete));
        assert_eq!(event_types(&events).last(), Some(&"response.incomplete"));
    }

    #[tokio::test]
    async fn replayed_text_events_preserve_logprobs() {
        let logprobs = serde_json::json!([{
            "token": "hello", "logprob": -0.125, "bytes": [104, 101, 108, 108, 111],
            "top_logprobs": [{"token": "hi", "logprob": -2.5, "bytes": [104, 105]}]
        }]);
        let resp = serde_json::json!({
            "id": "resp_1", "object": "response", "status": "completed",
            "output": [{
                "id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
                "content": [
                    {"type": "output_text", "text": "hello", "annotations": [], "logprobs": logprobs},
                    {"type": "output_text", "text": " world", "annotations": [], "logprobs": null},
                    {"type": "output_text", "text": "!", "annotations": []}
                ]
            }]
        });
        for degraded in [false, true] {
            let body = bytes::Bytes::from(serde_json::to_vec(&resp).unwrap());
            let outcome = if degraded {
                ManagedOutcome::Degraded {
                    status: 200,
                    content_type: "application/json".into(),
                    body,
                }
            } else {
                ManagedOutcome::Released {
                    binding: None,
                    status: 200,
                    content_type: "application/json".into(),
                    body,
                }
            };
            let response = respond_responses_sse(outcome);
            let body = axum::body::to_bytes(response.into_body(), usize::MAX)
                .await
                .unwrap();
            let events = sse_events(std::str::from_utf8(&body).unwrap());
            let added = events
                .iter()
                .find(|e| e["type"] == "response.content_part.added" && e["content_index"] == 0)
                .unwrap();
            assert_eq!(added["part"]["logprobs"], serde_json::json!([]));
            for (content_index, expected) in [
                (0, logprobs.clone()),
                (1, serde_json::json!([])),
                (2, serde_json::json!([])),
            ] {
                for kind in ["response.output_text.delta", "response.output_text.done"] {
                    let event = events
                        .iter()
                        .find(|e| e["type"] == kind && e["content_index"] == content_index)
                        .unwrap();
                    assert_eq!(event["logprobs"], expected);
                }
            }
            let done = events
                .iter()
                .find(|e| e["type"] == "response.content_part.done" && e["content_index"] == 0)
                .unwrap();
            assert_eq!(done["part"], resp["output"][0]["content"][0]);
            assert_eq!(events.last().unwrap()["response"], resp);
        }
    }

    #[test]
    fn degraded_responses_are_replayed_as_a_marked_stream() {
        let outcome = ManagedOutcome::Degraded {
            status: 200,
            content_type: "application/json".into(),
            body: bytes::Bytes::from_static(
                br#"{"id":"resp_1","object":"response","status":"completed","output":[]}"#,
            ),
        };
        let response = respond_responses_sse(outcome);
        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers()[header::CONTENT_TYPE],
            "text/event-stream"
        );
        assert_eq!(
            response.headers()["x-pc-degraded"],
            "passthrough_without_memory"
        );
    }

    #[test]
    fn upstream_errors_are_not_wrapped_in_a_stream() {
        let outcome = ManagedOutcome::Released {
            binding: None,
            status: 400,
            content_type: "application/json".into(),
            body: bytes::Bytes::from_static(br#"{"error":{"message":"bad"}}"#),
        };
        let response = respond_responses_sse(outcome);
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);
        assert_eq!(response.headers()[header::CONTENT_TYPE], "application/json");
    }

    #[test]
    fn responses_injection_follows_the_injection_switch_unless_opted_out() {
        assert!(injects(&Format::Chat, Injection::All, false, false));
        assert!(!injects(&Format::Responses, Injection::All, false, true));
        assert!(injects(&Format::Responses, Injection::All, true, false));
        assert!(!injects(&Format::Responses, Injection::Off, true, true));
        assert!(!injects(&Format::Chat, Injection::Off, true, true));
    }

    #[test]
    fn the_default_injects_only_requests_with_a_conversation_id() {
        assert!(injects(
            &Format::Chat,
            Injection::ConversationsOnly,
            true,
            true
        ));
        assert!(injects(
            &Format::Responses,
            Injection::ConversationsOnly,
            true,
            true
        ));
        assert!(!injects(
            &Format::Chat,
            Injection::ConversationsOnly,
            true,
            false
        ));
        assert!(!injects(
            &Format::Responses,
            Injection::ConversationsOnly,
            true,
            false
        ));
    }

    #[test]
    fn conversation_id_comes_only_from_a_valid_header() {
        let mut headers = HeaderMap::new();
        assert_eq!(conversation_id(&headers, &Format::Chat), None);
        headers.insert(CONVERSATION_ID_HEADER, "coding-client-001".parse().unwrap());
        assert_eq!(
            conversation_id(&headers, &Format::Chat).as_deref(),
            Some("powercontext:coding-client-001")
        );
        headers.insert(CONVERSATION_ID_HEADER, "has space".parse().unwrap());
        assert_eq!(conversation_id(&headers, &Format::Chat), None);
        headers.insert(CONVERSATION_ID_HEADER, "x".repeat(257).parse().unwrap());
        assert_eq!(conversation_id(&headers, &Format::Chat), None);
    }

    /// Codex 0.145 的请求头：同一会话的父线程与子代理线程共享 `session-id`，`thread-id` 不同。
    fn codex_headers(thread: &str) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert(
            "session-id",
            "019a0000-0000-7000-8000-000000000001".parse().unwrap(),
        );
        headers.insert("x-client-request-id", thread.parse().unwrap());
        headers.insert(CODEX_THREAD_ID_HEADER, thread.parse().unwrap());
        headers
    }

    #[test]
    fn codex_threads_are_separate_conversations_on_the_responses_route() {
        let root = codex_headers("019a0000-0000-7000-8000-00000000000a");
        let child = codex_headers("019a0000-0000-7000-8000-00000000000b");
        let root_id = conversation_id(&root, &Format::Responses);
        assert_eq!(
            root_id.as_deref(),
            Some("codex-thread:019a0000-0000-7000-8000-00000000000a")
        );
        assert_eq!(
            conversation_id(&root, &Format::Responses),
            root_id,
            "every request of a thread"
        );
        assert_ne!(conversation_id(&child, &Format::Responses), root_id);
        // 只在 Responses 入口识别 Codex 的线程头；宿主提供的会话 ID 优先。
        assert_eq!(conversation_id(&root, &Format::Chat), None);
        let mut hosted = root.clone();
        hosted.insert(CONVERSATION_ID_HEADER, "host-1".parse().unwrap());
        assert_eq!(
            conversation_id(&hosted, &Format::Responses).as_deref(),
            Some("powercontext:host-1")
        );
    }

    #[test]
    fn claude_code_subagents_are_separate_conversations() {
        // Claude Code 的主代理与子代理共享会话 ID，子代理的请求另带代理 ID。
        let mut main = HeaderMap::new();
        main.insert(CLAUDE_CODE_SESSION_HEADER, "session-1".parse().unwrap());
        let mut subagent = main.clone();
        subagent.insert(CLAUDE_CODE_AGENT_HEADER, "agent-a".parse().unwrap());
        let mut nested = main.clone();
        nested.insert(CLAUDE_CODE_AGENT_HEADER, "agent-b".parse().unwrap());
        nested.insert("x-claude-code-parent-agent-id", "agent-a".parse().unwrap());
        for format in [Format::Chat, Format::Responses] {
            let main_id = conversation_id(&main, &format);
            assert_eq!(main_id.as_deref(), Some("claude-code:session-1"));
            assert_eq!(
                conversation_id(&subagent, &format).as_deref(),
                Some("claude-code:session-1 agent-a")
            );
            assert_eq!(
                conversation_id(&nested, &format).as_deref(),
                Some("claude-code:session-1 agent-b")
            );
        }
        // 会话与代理的组合不会与另一个会话重合。
        let mut slash = HeaderMap::new();
        slash.insert(
            CLAUDE_CODE_SESSION_HEADER,
            "session-1/agent-a".parse().unwrap(),
        );
        assert_ne!(
            conversation_id(&slash, &Format::Chat),
            conversation_id(&subagent, &Format::Chat)
        );
    }

    #[test]
    fn opencode_child_sessions_are_separate_conversations() {
        // 子会话有自己的会话 ID，亲和头与父会话相同。
        let affinity = "ses_0123456789abcdef";
        let mut parent = HeaderMap::new();
        parent.insert(OPENCODE_SESSION_HEADER, "ses_parent".parse().unwrap());
        parent.insert("x-opencode-session", affinity.parse().unwrap());
        parent.insert("x-session-affinity", affinity.parse().unwrap());
        let mut child = parent.clone();
        child.insert(OPENCODE_SESSION_HEADER, "ses_child".parse().unwrap());
        child.insert(
            "x-opencode-parent-session-id",
            "ses_parent".parse().unwrap(),
        );
        for format in [Format::Chat, Format::Responses] {
            assert_eq!(
                conversation_id(&parent, &format).as_deref(),
                Some("opencode:ses_parent")
            );
            assert_eq!(
                conversation_id(&child, &format).as_deref(),
                Some("opencode:ses_child")
            );
        }
        // 只有亲和头时不算会话。
        let mut affinity_only = HeaderMap::new();
        affinity_only.insert("x-opencode-session", affinity.parse().unwrap());
        affinity_only.insert("x-session-affinity", affinity.parse().unwrap());
        assert_eq!(conversation_id(&affinity_only, &Format::Chat), None);
    }

    #[test]
    fn the_powercontext_header_overrides_every_native_identity() {
        let mut headers = codex_headers("thread-a");
        headers.insert(CLAUDE_CODE_SESSION_HEADER, "session-1".parse().unwrap());
        headers.insert(OPENCODE_SESSION_HEADER, "ses_parent".parse().unwrap());
        headers.insert(CONVERSATION_ID_HEADER, "host-1".parse().unwrap());
        for format in [Format::Chat, Format::Responses] {
            assert_eq!(
                conversation_id(&headers, &format).as_deref(),
                Some("powercontext:host-1")
            );
        }
    }

    #[test]
    fn chat_ingress_null_content_without_tool_calls_also_preserved() {
        let body =
            r#"{"model":"m","messages":[{"role":"assistant","content":null}],"max_tokens":8}"#;
        let chat: OpenAiChatRequest = serde_json::from_str(body).unwrap();
        let canonical = to_canonical_chat(chat).unwrap();
        assert!(canonical.messages[0].content_null);
        assert!(canonical.messages[0].tool_calls.is_none());
    }

    #[derive(Clone, Default)]
    struct EvidenceOutput(Arc<std::sync::Mutex<Vec<u8>>>);
    impl std::io::Write for EvidenceOutput {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(bytes);
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    #[tokio::test]
    async fn real_ingress_exports_usage_and_content_boundaries_without_sensitive_data() {
        use pc_gateway_evidence::JsonEvidenceSink;
        use tower::ServiceExt;
        let output = EvidenceOutput::default();
        let sink = Arc::new(
            JsonEvidenceSink::new(output.clone(), std::num::NonZeroUsize::new(1024).unwrap())
                .unwrap(),
        );
        let (router, server) = lifecycle_router_with_evidence(sink.clone()).await;
        // Exercise JSON/SSE on both public routes with the real reqwest transport.
        for i in 0..200 {
            let chat = i % 4 < 2;
            let stream = i % 2 == 1;
            let request = Request::builder().method("POST")
                .uri(if chat { "/gateway/v1/chat/completions" } else { "/gateway/v1/responses" })
                .header(header::AUTHORIZATION, "Bearer gateway-key")
                .header("x-powercontext-request-id", "SYNTHETIC_SENSITIVE_CORRELATION")
                .body(axum::body::Body::from(if chat {
                    serde_json::json!({"model":"SYNTHETIC_SENSITIVE_MODEL", "messages":[{"role":"user", "content":"SYNTHETIC_SENSITIVE_PROMPT"}], "stream":stream}).to_string()
                } else {
                    serde_json::json!({"model":"SYNTHETIC_SENSITIVE_MODEL", "input":[{"role":"user", "content":"SYNTHETIC_SENSITIVE_PROMPT"}], "store":false, "stream":stream}).to_string()
                })).unwrap();
            let response = router.clone().oneshot(request).await.unwrap();
            assert!(response.status().is_success());
            axum::body::to_bytes(response.into_body(), 1024 * 1024)
                .await
                .unwrap();
        }
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            while sink.stats().written < 1200 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .unwrap();
        assert_eq!(sink.stats().dropped, 0);
        let text = String::from_utf8(output.0.lock().unwrap().clone()).unwrap();
        assert!(!text.contains("SYNTHETIC_SENSITIVE"));
        assert!(!text.contains("gateway-key"));
        assert!(!text.contains("/v1/"));
        let mut records = std::collections::BTreeMap::<String, Vec<serde_json::Value>>::new();
        for line in text.lines() {
            let value: serde_json::Value = serde_json::from_str(line).unwrap();
            records
                .entry(value["evidence"]["call_id"].as_str().unwrap().to_owned())
                .or_default()
                .push(value);
        }
        assert_eq!(records.len(), 200);
        for events in records.values() {
            let names: Vec<_> = events
                .iter()
                .map(|e| e["event"].as_str().unwrap())
                .collect();
            assert_eq!(
                names,
                ["attempt", "call", "delivery", "delivery", "delivery", "delivery"]
            );
            let call = &events[1]["evidence"];
            let attempt = &events[0]["evidence"];
            let chat = call["usage_mapping"] == "open_ai_chat";
            assert_eq!(
                call["usage"]["input"]["tokens"],
                if chat { 100 } else { 80 }
            );
            assert_eq!(
                call["usage"]["cache_read"]["tokens"],
                if chat { 70 } else { 30 }
            );
            assert!(call["usage"]["cache_write"]["tokens"].is_null());
            assert!(call["upstream_ttft_ms"].is_null());
            assert!(attempt["response_first_byte_ms"].is_u64());
            assert!(
                attempt["response_complete_ms"].as_u64()
                    >= attempt["response_first_byte_ms"].as_u64()
            );
            assert_eq!(events[2]["evidence"]["boundary"], "framework_handoff");
            assert_eq!(events[3]["evidence"]["boundary"], "first_body_frame");
            assert_eq!(events[4]["evidence"]["boundary"], "first_model_content");
            assert_eq!(events[5]["evidence"]["boundary"], "body_completed");
            assert!(
                events[4]["evidence"]["elapsed_ms"].as_u64()
                    >= events[3]["evidence"]["elapsed_ms"].as_u64()
            );
            assert!(
                events[2]["evidence"]["elapsed_ms"].as_u64()
                    >= call["release_authorized_ms"].as_u64()
            );
        }
        server.abort();
    }

    #[tokio::test]
    async fn unconsumed_response_reports_drop_without_claiming_content_delivery() {
        use pc_gateway_evidence::{BufferedEvidenceSink, DeliveryBoundary, EvidenceEvent};
        use tower::ServiceExt;
        let evidence = Arc::new(BufferedEvidenceSink::new());
        let (router, server) = lifecycle_router_with_evidence(evidence.clone()).await;
        let request = Request::builder()
            .method("POST")
            .uri("/gateway/v1/chat/completions")
            .header(header::AUTHORIZATION, "Bearer gateway-key")
            .body(axum::body::Body::from(
                r#"{"model":"test","messages":[{"role":"user","content":"hello"}]}"#,
            ))
            .unwrap();
        let response = router.oneshot(request).await.unwrap();
        drop(response);
        let delivery: Vec<_> = evidence
            .events()
            .into_iter()
            .filter_map(|e| {
                if let EvidenceEvent::Delivery(d) = e {
                    Some(d.boundary)
                } else {
                    None
                }
            })
            .collect();
        assert!(matches!(
            delivery.as_slice(),
            [
                DeliveryBoundary::FrameworkHandoff,
                DeliveryBoundary::BodyDropped
            ]
        ));
        server.abort();
    }
}
