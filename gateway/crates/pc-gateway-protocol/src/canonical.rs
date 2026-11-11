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

use serde::{Deserialize, Serialize};

/// 网关内部规范请求：发送前必须完整落入 input/check。
/// 只保留结构性字段，语义内容由 Core 裁决。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CanonicalRequest {
    pub model: String,
    pub messages: Vec<CanonicalMessage>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub tools: Vec<serde_json::Value>,
    /// Responses API 的 instructions（系统级指令）；chat 格式为 None。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub instructions: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub native_cache_fields: Option<serde_json::Value>,
    /// 显式输出限制（与输入预算分开；见 RFC「补充上下文字节与完整请求 token」）。
    pub output_limits: OutputLimits,
    /// 是否请求上游流式（SSE）。strict 语义不变：网关全量缓冲并过
    /// before_release 后才向客户端释放；流式的是上游接收路径。
    #[serde(default)]
    pub stream: bool,
    /// 结构化输出约束（response_format），原样透传。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub response_format: Option<serde_json::Value>,
    /// 工具选择策略（tool_choice），原样透传。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_choice: Option<serde_json::Value>,
    /// Responses 的保留设置（store）。#2313 要求 openai-responses 目标显式 store:false。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub store: Option<bool>,
    /// 调用方使用的 token 上限字段名（"max_tokens" 或 "max_completion_tokens"），
    /// 渲染器按原名输出——o-series 只认 max_completion_tokens。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token_limit_field: Option<String>,
    /// Responses 的会话续写 ID，原样透传给上游和 Core。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous_response_id: Option<String>,
    /// Responses 的会话标识（conversation 参数），原样透传。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conversation: Option<String>,
    /// 入口未建模的其余顶层请求字段（如 reasoning、include、prompt_cache_key），原样透传。
    /// 是否允许发送由 Core 的请求字段白名单裁决：被拒是显式 withhold，而不是静默丢弃。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub passthrough_fields: Option<serde_json::Map<String, serde_json::Value>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CanonicalMessage {
    pub role: Role,
    pub content: Vec<serde_json::Value>,
    /// 原始 content 为 JSON null（assistant 发起工具调用时的规范形态）。
    /// 渲染时必须还原为 null 而非空数组，否则上游校验拒绝。
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub content_null: bool,
    /// assistant 消息的工具调用声明（chat 线格式），原样透传不解释。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_calls: Option<Vec<serde_json::Value>>,
    /// tool 消息的结果配对 id，原样透传（丢失即破坏工具循环）。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_call_id: Option<String>,
    /// Additional native message fields, included in the final payload checked by Core.
    #[serde(default, skip_serializing_if = "serde_json::Map::is_empty")]
    pub extra_fields: serde_json::Map<String, serde_json::Value>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    System,
    Developer,
    User,
    Assistant,
    Tool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct OutputLimits {
    /// None = 客户端未指定，不输出该字段（上游用默认值，不截断）。
    pub max_output_tokens: Option<u32>,
}

/// 渲染结果：语义内容不变的最终字节。
#[derive(Debug, Clone)]
pub struct RenderedPayload {
    pub bytes: bytes::Bytes,
    pub content_type: &'static str,
}
