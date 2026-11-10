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

use crate::sender::{
    AdmittedSend, NotSentReason, RawResponse, SendIndeterminateReason, SendOutcome, Transport,
};
use bytes::Bytes;
use std::future::Future;
use std::time::{Duration, Instant};

/// 基于 reqwest 的传输实现：裸 HTTP 直连上游，不经任何厂商 SDK。
///
/// 结果映射（对齐 RFC 失败语义）：
/// - 连接失败/请求构造失败 → 确定性 NotSent（可重试）；
/// - 发送后连接丢失、超时、解码中断 → Indeterminate（不自动重发）；
/// - 流式接收：逐块读入（计量首字节延迟、字节数受 `max_buffer_bytes` 上限），
///   strict 语义不变——完整缓冲后仍需过 before_release 才释放。
#[derive(Debug, Clone)]
pub struct ReqwestTransport {
    client: reqwest::Client,
    /// 流式/非流式响应的缓冲字节上限（容量保护）。
    max_buffer_bytes: usize,
    subscription_responses_sse: bool,
    codeassist_chat: bool,
    copilot_chat: bool,
}

impl ReqwestTransport {
    pub fn new(timeout: Duration) -> Self {
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .timeout(timeout)
            .build()
            .expect("reqwest client build");
        Self {
            client,
            max_buffer_bytes: pc_gateway_types::CONTEXT_CHECK_BODY_LIMIT,
            subscription_responses_sse: false,
            codeassist_chat: false,
            copilot_chat: false,
        }
    }

    pub fn with_buffer_limit(mut self, max_buffer_bytes: usize) -> Self {
        self.max_buffer_bytes = max_buffer_bytes.min(pc_gateway_types::CONTEXT_CHECK_BODY_LIMIT);
        self
    }

    pub fn with_subscription_responses_sse(mut self, enabled: bool) -> Self {
        self.subscription_responses_sse = enabled;
        self
    }

    pub fn with_codeassist_chat(mut self, enabled: bool) -> Self {
        self.codeassist_chat = enabled;
        self
    }

    pub fn with_copilot_chat(mut self, enabled: bool) -> Self {
        self.copilot_chat = enabled;
        self
    }
}

impl Default for ReqwestTransport {
    fn default() -> Self {
        Self::new(Duration::from_secs(120))
    }
}

impl Transport for ReqwestTransport {
    fn send<'a>(&'a self, send: AdmittedSend) -> impl Future<Output = SendOutcome> + Send + 'a {
        Box::pin(async move {
            let model = if self.codeassist_chat {
                serde_json::from_slice::<serde_json::Value>(&send.payload)
                    .ok()
                    .and_then(|v| v["model"].as_str().map(String::from))
            } else {
                None
            };
            let mut req = self.client.post(&send.target_url);
            let mut has_content_type = false;
            for (k, v) in &send.headers {
                if k.eq_ignore_ascii_case("content-type") {
                    has_content_type = true;
                }
                req = req.header(k, v);
            }
            if !has_content_type {
                req = req.header("content-type", "application/json");
            }
            let start = Instant::now();
            let resp = match req.body(send.payload).send().await {
                Err(e) => {
                    // NotSent 仅限于「可证明发生在传输之前」的失败：
                    // - is_connect()：TCP/TLS 握手未完成，body 必然未发出；
                    // - is_builder()：请求构造阶段，早于一切网络动作。
                    // 其余（is_request/is_timeout/is_body/is_decode）都无法排除
                    // body 已完整送达上游的可能——reqwest 的 is_request() 尤其
                    // 涵盖「对端读完全部 body 后关闭/超时」，按未送达重试会
                    // 重复计费。一律归 Indeterminate，不重试。
                    return if e.is_connect() {
                        SendOutcome::NotSent(NotSentReason::ConnectRefused)
                    } else if e.is_builder() {
                        SendOutcome::NotSent(NotSentReason::RejectedLocally)
                    } else if e.is_timeout() {
                        SendOutcome::Indeterminate(SendIndeterminateReason::TimeoutAwaitingResponse)
                    } else {
                        SendOutcome::Indeterminate(SendIndeterminateReason::LostAfterHandoff)
                    };
                }
                Ok(resp) => resp,
            };
            let status = resp.status().as_u16();
            let mut content_type = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("application/json")
                .to_string();

            // 两种接收模式共用逐块累积 + 字节上限（review P2：非流式路径
            // 此前用 resp.bytes() 一次性收集，max_buffer_bytes 完全不生效）。
            // 首字节是 HTTP 正文的首个非空 chunk，不是模型首 token。
            let mut body: Vec<u8> = Vec::with_capacity(16 * 1024);
            let mut first_byte: Option<u64> = None;
            let mut stream = resp.bytes_stream();
            loop {
                match next_chunk(&mut stream).await {
                    None => break,
                    Some(Err(reason)) => {
                        return SendOutcome::IncompleteResponse {
                            reason,
                            status,
                            response_first_byte_ms: first_byte,
                        }
                    }
                    Some(Ok(chunk)) => {
                        if !chunk.is_empty() && first_byte.is_none() {
                            first_byte = Some(start.elapsed().as_millis() as u64);
                        }
                        if chunk.len() > self.max_buffer_bytes.saturating_sub(body.len()) {
                            // 已送达且部分计费：不重发，向上标记不确定。
                            return SendOutcome::IncompleteResponse {
                                reason: SendIndeterminateReason::BufferLimitExceeded,
                                status,
                                response_first_byte_ms: first_byte,
                            };
                        }
                        body.extend_from_slice(&chunk);
                    }
                }
            }
            let complete_ms = start.elapsed().as_millis() as u64;
            if self.copilot_chat
                && send.target_url.ends_with("/chat/completions")
                && (200..300).contains(&status)
            {
                match pc_gateway_protocol::copilot_chat_response(&body) {
                    Some(normalized) => {
                        body = normalized;
                        content_type = "application/json".into();
                    }
                    None => {
                        return SendOutcome::IncompleteResponse {
                            reason: SendIndeterminateReason::LostAfterHandoff,
                            status,
                            response_first_byte_ms: first_byte,
                        }
                    }
                }
            }
            if self.codeassist_chat && (200..300).contains(&status) {
                match model
                    .as_deref()
                    .and_then(|model| pc_gateway_protocol::codeassist_chat_response(&body, model))
                {
                    Some(normalized) => {
                        body = normalized;
                        content_type = "application/json".into();
                    }
                    None => {
                        return SendOutcome::IncompleteResponse {
                            reason: SendIndeterminateReason::LostAfterHandoff,
                            status,
                            response_first_byte_ms: first_byte,
                        }
                    }
                }
            }
            if self.subscription_responses_sse
                && send.target_url.ends_with("/responses")
                && (200..300).contains(&status)
            {
                // These subscription routes explicitly request SSE. Like the upstream SDK,
                // decode that protocol even when a peer omits Content-Type; a complete
                // terminal response is still mandatory before Core can check release.
                let terminal = pc_gateway_protocol::buffered_subscription_response(&body);
                match terminal {
                    Some(terminal) => {
                        body = terminal;
                        content_type = "application/json".into();
                    }
                    None => {
                        return SendOutcome::IncompleteResponse {
                            reason: SendIndeterminateReason::LostAfterHandoff,
                            status,
                            response_first_byte_ms: first_byte,
                        }
                    }
                }
            }
            if body.len() > self.max_buffer_bytes {
                return SendOutcome::IncompleteResponse {
                    reason: SendIndeterminateReason::BufferLimitExceeded,
                    status,
                    response_first_byte_ms: first_byte,
                };
            }
            let usage = extract_usage(&body, &content_type);
            SendOutcome::Delivered(RawResponse {
                status,
                body: Bytes::from(body),
                content_type,
                response_first_byte_ms: first_byte,
                response_complete_ms: Some(complete_ms),
                usage,
            })
        })
    }

    fn cancel<'a>(&'a self, _send: &'a AdmittedSend) -> impl Future<Output = ()> + Send + 'a {
        // 非流式/全量缓冲调用在响应完整返回后已无进行中的上游工作；
        // 放弃响应体即断开连接（连接池按目标隔离）。
        std::future::ready(())
    }
}

fn extract_usage(body: &[u8], content_type: &str) -> Option<serde_json::Value> {
    let media_type = content_type.split(';').next().unwrap_or("").trim();
    if !media_type.eq_ignore_ascii_case("text/event-stream") {
        return serde_json::from_slice::<serde_json::Value>(body)
            .ok()
            .and_then(|v| v.get("usage").filter(|v| v.is_object()).cloned());
    }
    // Usage-only Chat chunks and Responses terminal events. Do not infer missing usage.
    // Join multi-line SSE data, reset at blank lines, and retain only the latest usage.
    let text = std::str::from_utf8(body).ok()?;
    // SSE ignores one BOM at stream start; keep the original response bytes intact.
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut data = String::new();
    let mut usage = None;
    for line in text.lines().chain(std::iter::once("")) {
        if line.is_empty() {
            if let Ok(value) = serde_json::from_str::<serde_json::Value>(&data) {
                let found = value
                    .get("usage")
                    .or_else(|| value.get("response")?.get("usage"));
                if let Some(found) = found.filter(|v| v.is_object()) {
                    usage = Some(found.clone());
                }
            }
            data.clear();
        } else if let Some(line) = line.strip_prefix("data:") {
            if !data.is_empty() {
                data.push('\n');
            }
            data.push_str(line.strip_prefix(' ').unwrap_or(line));
        }
    }
    usage
}

/// 不引入 futures-util 依赖的 next()：用标准库 poll_fn 轮询 Stream。
async fn next_chunk<S>(stream: &mut S) -> Option<Result<Bytes, SendIndeterminateReason>>
where
    S: futures_core::Stream<Item = reqwest::Result<Bytes>> + Unpin,
{
    use std::pin::Pin;
    std::future::poll_fn(|cx| Pin::new(&mut *stream).poll_next(cx))
        .await
        .map(|r| {
            r.map_err(|error| {
                // Preserve the failure category after headers too, without retaining
                // or exporting reqwest errors (which can contain URLs/credentials).
                if error.is_timeout() {
                    SendIndeterminateReason::TimeoutAwaitingResponse
                } else {
                    SendIndeterminateReason::LostAfterHandoff
                }
            })
        })
}
