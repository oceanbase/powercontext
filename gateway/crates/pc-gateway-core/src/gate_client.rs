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

use pc_gateway_types::{
    CaptureContentSourceRequest, ContextCheckRequest, ContextCheckResult, PrepareContextRequest,
    PrepareContextResult,
};
use std::future::Future;

/// Core 检查的客户端抽象（PR #2199：单端点两阶段契约）。
/// 实现：TCP（独立部署形态）或 UDS（同机组合部署形态）；
/// 嵌入式实现直接进程内调用等价算法。
///
/// Gate 不可用 → `GateUnavailable`。是否降级为无记忆直通由调用方
/// 的 `DegradePolicy` 决定（仅对未配置强制规则的 Scope 允许）。
pub trait GateClient: Send + Sync {
    fn check(
        &self,
        request: ContextCheckRequest,
    ) -> impl Future<Output = Result<ContextCheckResult, GateError>> + Send;

    /// GET_CAPABILITIES 能力发现：获知支持的模式、必需规则、可降级性等。
    fn capabilities(
        &self,
    ) -> impl Future<Output = Result<serde_json::Value, GateError>> + Send;

    /// PREPARE_CONTEXT：检索并装配注入内容（智力全在 Core；网关只放置）。
    fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> impl Future<Output = Result<PrepareContextResult, GateError>> + Send;

    /// CAPTURE_CONTENT_SOURCE：沉淀一轮过检对话（同步落库；提取在 PC 后台）。
    /// best-effort：失败只记证据，不影响已过检响应的交付语义。
    fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> impl Future<Output = Result<serde_json::Value, GateError>> + Send;
}

#[derive(Debug, thiserror::Error)]
pub enum GateError {
    #[error("gate unavailable: {0}")]
    GateUnavailable(String),
    #[error("gate response malformed: {0}")]
    Malformed(String),
}
