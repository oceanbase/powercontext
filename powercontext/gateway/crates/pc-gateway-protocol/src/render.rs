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

use crate::{CanonicalRequest, RenderedPayload};

/// 渲染错误。
#[derive(Debug, thiserror::Error)]
pub enum RenderError {
    #[error("unsupported field for renderer {renderer}: {field}")]
    UnsupportedField { renderer: &'static str, field: String },
    #[error("cache render plan invalid for renderer {renderer}")]
    InvalidRenderPlan { renderer: &'static str },
}

/// 线格式渲染器。OpenAI 系实现共享同一份 chat-completions 渲染
/// （借鉴 TensorZero 的 chat_completions 共享模块模式）。
///
/// v1 strict 语义：完整渲染最终字节交给 input/check；
/// 缓存渲染计划（withhold 附带）只允许合并计划里显式列出的顶层字段。
pub trait WireRenderer: Send + Sync {
    fn name(&self) -> &'static str;
    fn render(
        &self,
        request: &CanonicalRequest,
        cache_render_plan: Option<&serde_json::Value>,
    ) -> Result<RenderedPayload, RenderError>;
}

/// OpenAI 兼容 chat-completions 渲染器。
///
/// 不依赖厂商 SDK，直接序列化线格式（对齐 RFC：高层包装无法证明
/// 覆盖最终序列化、内部重试与原始错误）。strict v1 固定 `stream: false`，
/// 上游以完整单元返回，SSE 分帧在 output/check 之后由网关侧完成。
#[derive(Debug, Default, Clone, Copy)]
pub struct OpenAiChatRenderer;

impl WireRenderer for OpenAiChatRenderer {
    fn name(&self) -> &'static str {
        "openai-chat-completions"
    }

    fn render(
        &self,
        request: &CanonicalRequest,
        cache_render_plan: Option<&serde_json::Value>,
    ) -> Result<RenderedPayload, RenderError> {
        // OpenAI 线格式：纯文本 content 用字符串形式（部分兼容后端不接受
        // 无 type 的部件数组）；多部件 content 按原样透传。
        let mut messages = Vec::with_capacity(request.messages.len());
        for m in &request.messages {
            let content: serde_json::Value = match m.content.as_slice() {
                [serde_json::Value::String(s)] => serde_json::Value::String(s.clone()),
                other => serde_json::to_value(other).map_err(|e| RenderError::UnsupportedField {
                    renderer: self.name(),
                    field: e.to_string(),
                })?,
            };
            messages.push(serde_json::json!({
                "role": m.role,
                "content": content,
            }));
        }

        // 只写入确定性的结构字段；不写请求 ID/时间戳（缓存前缀保真）。
        let mut body = serde_json::json!({
            "model": request.model,
            "messages": messages,
            "max_tokens": request.output_limits.max_output_tokens,
            "stream": request.stream,
        });
        if !request.tools.is_empty() {
            body["tools"] = serde_json::json!(request.tools);
        }
        if let Some(fields) = request.native_cache_fields.as_ref() {
            merge_top_level(&mut body, fields).map_err(|_| RenderError::InvalidRenderPlan {
                renderer: self.name(),
            })?;
        }
        if let Some(plan) = cache_render_plan {
            merge_top_level(&mut body, plan).map_err(|_| RenderError::InvalidRenderPlan {
                renderer: self.name(),
            })?;
        }

        let bytes = serde_json::to_vec(&body)
            .map_err(|_| RenderError::InvalidRenderPlan {
                renderer: self.name(),
            })?
            .into();
        Ok(RenderedPayload {
            bytes,
            content_type: "application/json",
        })
    }
}

/// 双格式渲染器：入口格式即上游格式（同格式进出，无跨格式转换）。
#[derive(Debug, Clone, Copy)]
pub enum GatewayRenderer {
    Chat(OpenAiChatRenderer),
    Responses(OpenAiResponsesRenderer),
}

impl Default for GatewayRenderer {
    fn default() -> Self {
        GatewayRenderer::Chat(OpenAiChatRenderer)
    }
}

impl WireRenderer for GatewayRenderer {
    fn name(&self) -> &'static str {
        match self {
            GatewayRenderer::Chat(r) => r.name(),
            GatewayRenderer::Responses(r) => r.name(),
        }
    }

    fn render(
        &self,
        request: &CanonicalRequest,
        cache_render_plan: Option<&serde_json::Value>,
    ) -> Result<RenderedPayload, RenderError> {
        match self {
            GatewayRenderer::Chat(r) => r.render(request, cache_render_plan),
            GatewayRenderer::Responses(r) => r.render(request, cache_render_plan),
        }
    }
}

/// 只合并获准的顶层键；src 必须是对象。
fn merge_top_level(dst: &mut serde_json::Value, src: &serde_json::Value) -> Result<(), ()> {
    let src_obj = src.as_object().ok_or(())?;
    let dst_obj = dst.as_object_mut().ok_or(())?;
    for (k, v) in src_obj {
        dst_obj.insert(k.clone(), v.clone());
    }
    Ok(())
}

/// OpenAI Responses API 渲染器。
///
/// 同格式进出：客户端用 Responses 格式进来，上游也渲染为 Responses；
/// 不做 chat ↔ responses 的跨格式转换（保真优先）。
/// content 里携带的 Responses 专有 item（function_call、function_call_output、
/// item_reference、reasoning 等）按原样作为顶层 input item 透出。
#[derive(Debug, Default, Clone, Copy)]
pub struct OpenAiResponsesRenderer;

/// Responses API 的顶层 input item 类型（区别于消息内 content part 的
/// input_text/output_text 等文本部件）。
const RESPONSES_ITEM_TYPES: &[&str] = &[
    "message",
    "function_call",
    "function_call_output",
    "custom_tool_call",
    "custom_tool_call_output",
    "item_reference",
    "reasoning",
    "web_search_call",
    "file_search_call",
    "computer_call",
    "computer_call_output",
    "image_generation_call",
    "code_interpreter_call",
    "mcp_call",
    "mcp_list_tools",
];

fn is_responses_item(v: &serde_json::Value) -> bool {
    v.get("type")
        .and_then(|t| t.as_str())
        .map(|t| RESPONSES_ITEM_TYPES.contains(&t))
        .unwrap_or(false)
}

impl WireRenderer for OpenAiResponsesRenderer {
    fn name(&self) -> &'static str {
        "openai-responses"
    }

    fn render(
        &self,
        request: &CanonicalRequest,
        cache_render_plan: Option<&serde_json::Value>,
    ) -> Result<RenderedPayload, RenderError> {
        let mut input = Vec::with_capacity(request.messages.len());
        for msg in &request.messages {
            // 单元素 content 且是 Responses 专有 item → 原样作为顶层 item。
            if msg.content.len() == 1 && is_responses_item(&msg.content[0]) {
                input.push(msg.content[0].clone());
                continue;
            }
            let content: serde_json::Value = serde_json::to_value(&msg.content)
                .map_err(|e| RenderError::UnsupportedField {
                    renderer: self.name(),
                    field: e.to_string(),
                })?;
            input.push(serde_json::json!({
                "role": msg.role,
                "content": content,
            }));
        }

        let mut body = serde_json::json!({
            "model": request.model,
            "input": input,
            "max_output_tokens": request.output_limits.max_output_tokens,
            "stream": request.stream,
        });
        if let Some(instructions) = &request.instructions {
            body["instructions"] = serde_json::json!(instructions);
        }
        if !request.tools.is_empty() {
            body["tools"] = serde_json::json!(request.tools);
        }
        if let Some(fields) = request.native_cache_fields.as_ref() {
            merge_top_level(&mut body, fields).map_err(|_| RenderError::InvalidRenderPlan {
                renderer: self.name(),
            })?;
        }
        if let Some(plan) = cache_render_plan {
            merge_top_level(&mut body, plan).map_err(|_| RenderError::InvalidRenderPlan {
                renderer: self.name(),
            })?;
        }

        let bytes = serde_json::to_vec(&body)
            .map_err(|_| RenderError::InvalidRenderPlan {
                renderer: self.name(),
            })?
            .into();
        Ok(RenderedPayload {
            bytes,
            content_type: "application/json",
        })
    }
}
