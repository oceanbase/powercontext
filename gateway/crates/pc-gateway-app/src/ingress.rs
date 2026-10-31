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

use crate::{proxy::SharedProxy, AppState};
use axum::body::Bytes;
use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use pc_gateway_core::{ManagedCall, ManagedOutcome};
use pc_gateway_protocol::{CanonicalMessage, CanonicalRequest, OutputLimits, Role};
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
    #[serde(default)]
    stream: Option<bool>,
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
}

/// Responses API 入口请求（同格式进出，不与 chat 互转）。
#[derive(Debug, Deserialize)]
struct OpenAiResponsesRequest {
    model: String,
    input: Vec<serde_json::Value>,
    #[serde(default)]
    instructions: Option<String>,
    #[serde(default)]
    tools: Vec<serde_json::Value>,
    #[serde(default)]
    max_output_tokens: Option<u32>,
    #[serde(default)]
    stream: Option<bool>,
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
    // 网关密钥认证（替代服务商 key；上游凭据由受控配置持有）。
    if let Some(expected) = &state.api_key {
        let provided = headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "));
        if provided != Some(expected.as_str()) {
            return (
                StatusCode::UNAUTHORIZED,
                Json(serde_json::json!({ "error": "invalid_gateway_key" })),
            )
                .into_response();
        }
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
                .into_response()
        }
    };
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
            let r: OpenAiResponsesRequest = match serde_json::from_slice(&body) {
                Ok(c) => c,
                Err(e) => {
                    return (
                        StatusCode::BAD_REQUEST,
                        Json(serde_json::json!({ "error": "invalid_responses_body", "detail": e.to_string() })),
                    )
                        .into_response()
                }
            };
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
    let renderer = match format {
        Format::Chat => pc_gateway_protocol::GatewayRenderer::Chat(Default::default()),
        Format::Responses => pc_gateway_protocol::GatewayRenderer::Responses(Default::default()),
    };

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
        degrade_policy: state.degrade,
        inject_context: state.inject_context
            && matches!(format, Format::Chat),
        sediment: state.sediment,
        prepared_contexts: Vec::new(),
    };

    let outcome = state.executor.run(call, &renderer, state.evidence.as_ref()).await;
    respond(outcome)
}

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
            max_output_tokens: chat.max_tokens.unwrap_or(1024),
        },
        stream: chat.stream.unwrap_or(false),
    })
}

/// Responses 入口 → 规范类型：消息项照常映射；
/// Responses 专有 item（function_call 等）原样封进 content，
/// 由 `OpenAiResponsesRenderer` 按原样作为顶层 input item 透出（无跨格式转换）。
fn to_canonical_responses(r: OpenAiResponsesRequest) -> Result<CanonicalRequest, String> {
    let mut messages = Vec::with_capacity(r.input.len());
    for item in r.input {
        let role_str = item.get("role").and_then(|v| v.as_str()).unwrap_or("");
        let role = match role_str {
            "system" | "developer" => Role::System,
            "user" => Role::User,
            "assistant" => Role::Assistant,
            "tool" => Role::Tool,
            // 无 role 的顶层 item（function_call/function_call_output 等）：
            // 原样保留，role 占位为 tool。
            _ => Role::Tool,
        };
        if role_str.is_empty() {
            messages.push(CanonicalMessage {
                role: Role::Tool,
                content: vec![item],
                content_null: false,
                tool_calls: None,
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
            role,
            content,
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        });
    }
    Ok(CanonicalRequest {
        model: r.model,
        messages,
        tools: r.tools,
        instructions: r.instructions,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: r.max_output_tokens.unwrap_or(1024),
        },
        stream: r.stream.unwrap_or(false),
    })
}

/// 结果映射：不存在静默成功；降级直通必须显式标记。
fn respond(outcome: ManagedOutcome) -> Response {
    match outcome {
        ManagedOutcome::Released { status, content_type, body, .. } => {
            // 上游状态码与 Content-Type 原样透传（错误不伪装 200）。
            let code = StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY);
            (
                code,
                [(header::CONTENT_TYPE, content_type.as_str())],
                body.to_vec(),
            )
                .into_response()
        }
        ManagedOutcome::Degraded { status, content_type, body } => match Response::builder()
            .status(
                StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY),
            )
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
        ManagedOutcome::GatePaused(msg) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({ "decision": "gate_paused", "detail": msg })),
        )
            .into_response(),
    }
}

// 抑制未使用导入警告（Bytes 用于类型别名文档场景）。
#[allow(dead_code)]
fn _unused(_: Option<Bytes>) {}

#[cfg(test)]
mod tests {
    //! PR #2307 review P2：chat 工具元数据经入口 canonical 化的保真测试。
    use super::*;

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

    #[test]
    fn chat_ingress_null_content_without_tool_calls_also_preserved() {
        let body = r#"{"model":"m","messages":[{"role":"assistant","content":null}],"max_tokens":8}"#;
        let chat: OpenAiChatRequest = serde_json::from_str(body).unwrap();
        let canonical = to_canonical_chat(chat).unwrap();
        assert!(canonical.messages[0].content_null);
        assert!(canonical.messages[0].tool_calls.is_none());
    }
}
