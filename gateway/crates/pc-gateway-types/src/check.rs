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

//! PR #2264 定义的 `POST /v1/context/check` 真实契约。
//!
//! 关键语义（openapi/powercontext.yaml 原文）：
//! - HTTP success alone is not approval——只有完整覆盖的 allow 才许可对应阶段；
//! - 执行器必须在 deadline（valid_until / limits.send_seconds）前
//!   发送/释放**恰好获批的字节**；
//! - binding 是不透明字符串，绑定 phase、身份、目标、策略与精确载荷；
//! - correlate_until 只是关联窗口，绝不许可新的发送。

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const MODEL_JSON_PAYLOAD_SCHEMA: &str = "powercontext.model-json-payload.v1";
pub const CONTEXT_CHECK_SCHEMA: &str = "powercontext.context-check.v1";
pub const CONTEXT_CHECK_COMPLETION: &str = "complete";

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CheckPhase {
    BeforeSend,
    BeforeRelease,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CheckDecision {
    Allow,
    Block,
    Withhold,
}

/// 完整载荷信封：仅内容与协议头（凭据、传输覆盖头被禁止），body 为完整原始 JSON 文本。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelJsonPayload {
    pub schema: String,
    pub headers: BTreeMap<String, String>,
    pub body: String,
}

impl ModelJsonPayload {
    pub fn new(headers: BTreeMap<String, String>, body: String) -> Self {
        Self {
            schema: MODEL_JSON_PAYLOAD_SCHEMA.into(),
            headers,
            body,
        }
    }
}

/// prepare 复用信封的定位（binding + 文本位置；位置语义归 Core）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PreparedContextLocation {
    pub binding: String,
    pub location: ModelJsonTextLocation,
}

/// 线格式 JSON 指针 + 解码后 UTF-8 文本的半开字节区间。
/// 指针形如 /messages/{i}/content 或 /messages/{i}/content/{j}/text，
/// 必须指向 user 消息（PC 的 _message_text 强制校验）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelJsonTextLocation {
    pub schema: String,
    pub pointer: String,
    pub start_byte: u64,
    pub end_byte: u64,
}

pub const MODEL_JSON_TEXT_LOCATION_SCHEMA: &str = "powercontext.model-json-text-location.v1";

/// `check_context` 请求：按 phase 二选一（内部标签即 phase 字段）。
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum ContextCheckRequest {
    BeforeSend {
        scope_id: String,
        target_profile: String,
        payload: ModelJsonPayload,
        /// 线格式必填：无复用块时也必须显式携带空数组。
        prepared_contexts: Vec<PreparedContextLocation>,
    },
    BeforeRelease {
        scope_id: String,
        target_profile: String,
        /// 对应 before_send allow 返回的 binding。
        request_binding: String,
        payload: ModelJsonPayload,
        completion: String,
    },
}

/// `check_context` 响应。两阶段共用 allow/block/withhold：
/// before_release 的 allow 即放行决定，block 即拒绝释放。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextCheckResult {
    pub schema: String,
    pub phase: CheckPhase,
    pub decision: CheckDecision,
    /// 无正文原因类别。
    pub reason_codes: Vec<String>,
    pub coverage: ContextCheckCoverage,
    pub policy_version: String,
    /// RFC3339 时间；发送/释放必须在此之前完成。
    pub valid_until: String,
    /// 仅 before_send allow 有；绝不许可新发送。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub correlate_until: Option<String>,
    /// 仅 allow 有：不透明绑定字符串。
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binding: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextCheckCoverage {
    pub profile: String,
    pub complete: bool,
}

/// ContextProtectionCapabilities（能力发现；enforcing 是动态降级的依据）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextProtectionCapabilities {
    pub check_versions: Vec<String>,
    pub payload_versions: Vec<String>,
    pub reuse_versions: Vec<String>,
    pub reuse_families: Vec<String>,
    pub enforcing: bool,
    pub limits: ContextProtectionLimits,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContextProtectionLimits {
    pub payload_bytes: u64,
    pub binding_bytes: u64,
    pub prepared_contexts: u64,
    pub dependencies: u64,
    pub send_seconds: u64,
    pub correlate_seconds: u64,
}
