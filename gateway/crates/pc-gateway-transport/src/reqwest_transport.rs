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
/// - 流式接收：逐块读入（计量 TTFT、字节数受 `max_buffer_bytes` 上限），
///   strict 语义不变——完整缓冲后仍需过 before_release 才释放。
#[derive(Debug, Clone)]
pub struct ReqwestTransport {
    client: reqwest::Client,
    /// 流式/非流式响应的缓冲字节上限（容量保护）。
    max_buffer_bytes: usize,
}

impl ReqwestTransport {
    pub fn new(timeout: Duration) -> Self {
        let client = reqwest::Client::builder()
            .timeout(timeout)
            .build()
            .expect("reqwest client build");
        Self {
            client,
            max_buffer_bytes: 32 * 1024 * 1024,
        }
    }

    pub fn with_buffer_limit(mut self, max_buffer_bytes: usize) -> Self {
        self.max_buffer_bytes = max_buffer_bytes;
        self
    }
}

impl Default for ReqwestTransport {
    fn default() -> Self {
        Self::new(Duration::from_secs(120))
    }
}

impl Transport for ReqwestTransport {
    fn send<'a>(
        &'a self,
        send: AdmittedSend,
    ) -> impl Future<Output = SendOutcome> + Send + 'a {
        Box::pin(async move {
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
            let resp = match req.body(send.payload.to_vec()).send().await {
                Err(e) => {
                    return if e.is_connect() {
                        SendOutcome::NotSent(NotSentReason::ConnectRefused)
                    } else if e.is_builder() || e.is_request() {
                        SendOutcome::NotSent(NotSentReason::RejectedLocally)
                    } else if e.is_timeout() {
                        // 请求可能已被上游接收：不能当作未送达重试。
                        SendOutcome::Indeterminate(SendIndeterminateReason::TimeoutAwaitingResponse)
                    } else {
                        SendOutcome::Indeterminate(SendIndeterminateReason::LostAfterHandoff)
                    }
                }
                Ok(resp) => resp,
            };
            let status = resp.status().as_u16();
            let content_type = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("application/json")
                .to_string();

            if send.stream {
                // 流式接收：逐块累积 + TTFT 计量 + 字节上限。
                let start = Instant::now();
                let mut body: Vec<u8> = Vec::with_capacity(16 * 1024);
                let mut ttft: Option<u64> = None;
                let mut stream = resp.bytes_stream();
                loop {
                    match next_chunk(&mut stream).await {
                        None => break,
                        Some(Err(_)) => {
                            return SendOutcome::Indeterminate(
                                SendIndeterminateReason::LostAfterHandoff,
                            )
                        }
                        Some(Ok(chunk)) => {
                            if ttft.is_none() {
                                ttft = Some(start.elapsed().as_millis() as u64);
                            }
                            if body.len() + chunk.len() > self.max_buffer_bytes {
                                // 已送达且部分计费：不重发，向上标记不确定。
                                return SendOutcome::Indeterminate(
                                    SendIndeterminateReason::BufferLimitExceeded,
                                );
                            }
                            body.extend_from_slice(&chunk);
                        }
                    }
                }
                let usage = extract_usage(&body);
                SendOutcome::Delivered(RawResponse {
                    status,
                    body: Bytes::from(body),
                    content_type,
                    ttft_ms: ttft,
                    usage,
                })
            } else {
                match resp.bytes().await {
                    Err(_) => {
                        SendOutcome::Indeterminate(SendIndeterminateReason::LostAfterHandoff)
                    }
                    Ok(body) => {
                        let usage = extract_usage(&body);
                        SendOutcome::Delivered(RawResponse {
                            status,
                            body,
                            content_type,
                            ttft_ms: None,
                            usage,
                        })
                    }
                }
            }
        })
    }

    fn cancel<'a>(&'a self, _send: &'a AdmittedSend) -> impl Future<Output = ()> + Send + 'a {
        // 非流式/全量缓冲调用在响应完整返回后已无进行中的上游工作；
        // 放弃响应体即断开连接（连接池按目标隔离）。
        std::future::ready(())
    }
}

fn extract_usage(body: &[u8]) -> Option<serde_json::Value> {
    serde_json::from_slice::<serde_json::Value>(body)
        .ok()
        .and_then(|v| v.get("usage").cloned())
}

/// 不引入 futures-util 依赖的 next()：用标准库 poll_fn 轮询 Stream。
async fn next_chunk<S>(stream: &mut S) -> Option<Result<Bytes, String>>
where
    S: futures_core::Stream<Item = reqwest::Result<Bytes>> + Unpin,
{
    use std::pin::Pin;
    std::future::poll_fn(|cx| Pin::new(&mut *stream).poll_next(cx))
        .await
        .map(|r| r.map_err(|e| e.to_string()))
}
