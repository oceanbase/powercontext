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

use bytes::Bytes;
use std::future::Future;

/// 一次已获准的发送。
#[derive(Debug, Clone)]
pub struct AdmittedSend {
    /// before_send allow 返回的不透明绑定字符串；必须原样用于 before_release。
    pub binding: String,
    /// 最终获准字节（渲染产物，binding 绑定的就是这些字节）。
    pub payload: Bytes,
    /// 获准目标 URL；不得静默重映射。
    pub target_url: String,
    /// 目标协议要求的头部（凭据由受控配置解析，不经请求字段）。
    pub headers: Vec<(String, String)>,
    /// 上游流式（SSE）接收：逐块读入并计量，strict 语义不变。
    pub stream: bool,
}

/// 原始响应 + 传输结果，交给 output/check 后才允许释放。
#[derive(Debug)]
pub struct RawResponse {
    pub status: u16,
    pub body: Bytes,
    /// 响应的 Content-Type（SSE 为 text/event-stream），释放时原样使用。
    pub content_type: String,
    /// 上游首字节延迟（毫秒）；流式接收时计量，非流式为 None。
    pub ttft_ms: Option<u64>,
    /// usage 证据（不可用时为 None，标记 unknown，不记零）。
    pub usage: Option<serde_json::Value>,
}

/// 发送结果：确定性状态优先；不确定即不确定。
#[derive(Debug)]
pub enum SendOutcome {
    /// 成功取得完整原始响应（尚未过 output/check，不得释放）。
    Delivered(RawResponse),
    /// 确定尚未送达：可在有界次数内由唯一所有者重试。
    NotSent(NotSentReason),
    /// 可能已到达上游：返回不确定，不自动付费重发，不假设退费。
    Indeterminate(SendIndeterminateReason),
}

#[derive(Debug, thiserror::Error)]
pub enum NotSentReason {
    #[error("connection refused before request sent")]
    ConnectRefused,
    #[error("request rejected before body accepted")]
    RejectedLocally,
}

#[derive(Debug, thiserror::Error)]
pub enum SendIndeterminateReason {
    #[error("connection lost after request handed to transport")]
    LostAfterHandoff,
    #[error("timeout awaiting response")]
    TimeoutAwaitingResponse,
    /// 流式接收超过缓冲上限：请求已送达且部分计费，不重发。
    #[error("streaming buffer limit exceeded")]
    BufferLimitExceeded,
}

/// 传输器抽象；实现保证只发送 `AdmittedSend`（类型上无法构造未准入的发送）。
pub trait Transport: Send + Sync {
    fn send<'a>(
        &'a self,
        send: AdmittedSend,
    ) -> impl Future<Output = SendOutcome> + Send + 'a;

    /// 尽力取消上游（strict 检查失败/缓冲超限时必需）。
    /// RPITIT 声明以固化 Send 约束（trait 是公开 API）。
    fn cancel<'a>(&'a self, send: &'a AdmittedSend) -> impl Future<Output = ()> + Send + 'a;
}
