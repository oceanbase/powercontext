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

//! prepare 契约（PR #2264），字段形状对照真实响应校准。

use serde::{Deserialize, Serialize};

/// 注入块的标准包裹标记（与 PC 的 `_MARKERS` 对齐）：
/// check 会扫描载荷中所有字符串，含标记的字节必须被 prepared_contexts 区间覆盖。
pub const PREPARED_CONTEXT_MARKER_BEGIN: &str = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1";
pub const PREPARED_CONTEXT_MARKER_END: &str = "END_POWERCONTEXT_PREPARED_CONTEXT_V1";

pub const PREPARED_CONTEXT_SCHEMA: &str = "powercontext.prepared-context.v1";
pub const PREPARED_CONTEXT_WITH_REUSE_SCHEMA: &str = "powercontext.prepared-context-with-reuse.v1";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PrepareContextRequest {
    pub scope_id: String,
    pub query: String,
    #[serde(default = "default_max_bytes")]
    pub max_bytes: u32,
    #[serde(default)]
    pub include_reuse: bool,
    /// #2313 契约：限定召回家族。reuse 只支持 atomic-memory；
    /// 不指定 assembly 时 Core 默认召回 Experience/Topic → check 阶段拒绝。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub assembly: Option<ContextAssembly>,
}

/// 召回组合：限定家族和条目数。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextAssembly {
    pub sections: Vec<AssemblySection>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AssemblySection {
    pub family: String,
    pub limit: u32,
}

impl ContextAssembly {
    /// memory-only assembly（只召回 Atomic Memory）：#2313 支持的唯一 reuse 家族。
    /// limit 可通过 PC_GATEWAY_ASSEMBLY_LIMIT 环境变量配置（默认 8，
    /// 不超过 Server 的 context_assembly_max_entries）。
    pub fn memory_only() -> Self {
        let limit = std::env::var("PC_GATEWAY_ASSEMBLY_LIMIT")
            .ok()
            .and_then(|v| v.parse().ok())
            .filter(|v| *v > 0 && *v <= 8)
            .unwrap_or(8);
        Self {
            sections: vec![AssemblySection {
                family: "memory".into(),
                limit,
            }],
        }
    }
}

fn default_max_bytes() -> u32 {
    8192
}

/// prepare 响应：普通 PreparedContext 或带复用信封的 WithReuse（按 schema 判别）。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum PrepareContextResult {
    WithReuse {
        schema: String,
        context: PreparedContext,
        reuse: ContextReuse,
    },
    Plain(PreparedContext),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreparedContext {
    pub schema: String,
    /// ready 时为内容文本；empty 时为 None。
    pub status: String,
    pub content: Option<String>,
    pub content_bytes: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextReuse {
    pub schema: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub constraints: Option<ReuseConstraints>,
    /// RFC3339。
    pub expires_at: String,
    pub binding: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReuseConstraints {
    #[serde(default)]
    pub preserve: Vec<String>,
    #[serde(default)]
    pub refresh_on: Vec<String>,
}

impl PrepareContextResult {
    pub fn content(&self) -> Option<&str> {
        match self {
            PrepareContextResult::WithReuse { context, .. } => context.content.as_deref(),
            PrepareContextResult::Plain(c) => c.content.as_deref(),
        }
    }

    pub fn binding(&self) -> Option<&str> {
        match self {
            PrepareContextResult::WithReuse { reuse, .. } => Some(&reuse.binding),
            PrepareContextResult::Plain(_) => None,
        }
    }

    /// 复用绑定的到期时间（RFC3339）；无复用元数据时为 None。
    pub fn expires_at(&self) -> Option<&str> {
        match self {
            PrepareContextResult::WithReuse { reuse, .. } => Some(&reuse.expires_at),
            PrepareContextResult::Plain(_) => None,
        }
    }
}

/// 标准注入块：与 PC 的 `_MARKERS`/摘要校验对齐。
/// check 会核验区间内字节与 binding 签发内容一致，不得增删一字节。
pub fn wrap_prepared_block(content: &str) -> String {
    format!("{PREPARED_CONTEXT_MARKER_BEGIN}\n{content}\n{PREPARED_CONTEXT_MARKER_END}")
}

/// `POST /v1/sources/content`（capture_content_source）：
/// 同步持久入库（Source journal），提取由 PC 后台异步完成。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CaptureContentSourceRequest {
    pub scope_id: String,
    /// 调用方指定的稳定 ID（幂等键）：用 release binding 摘要生成。
    pub source_id: String,
    /// 原始整合内容（≤200000 字符）。仅沉淀过检放行的内容。
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<serde_json::Value>,
}
