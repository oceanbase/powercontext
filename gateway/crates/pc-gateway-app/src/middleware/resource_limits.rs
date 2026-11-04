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

//! 资源限流：请求体上限 + 并发上限（对齐 PC 的 HttpResourceLimitsMiddleware）。
//! 最外层准入——被拒请求不消耗内层任何资源。/healthz 豁免并发限制
//! （编排系统探活不应因业务拥堵被拒）。

use super::{reject, GatewayMiddleware};
use axum::{extract::Request, http::StatusCode, middleware::Next, response::Response};
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::task::{Context, Poll};
use tokio::sync::Semaphore;

pub struct ResourceLimits {
    max_body_bytes: usize,
    max_concurrent: Arc<Semaphore>,
    /// 被拒计数（无正文证据）。
    rejected_body: AtomicU64,
    rejected_concurrency: AtomicU64,
}

impl ResourceLimits {
    pub fn new(max_body_bytes: usize, max_concurrent: usize) -> Arc<Self> {
        Arc::new(Self {
            max_body_bytes,
            max_concurrent: Arc::new(Semaphore::new(max_concurrent)),
            rejected_body: AtomicU64::new(0),
            rejected_concurrency: AtomicU64::new(0),
        })
    }
}

impl GatewayMiddleware for ResourceLimits {
    fn name(&self) -> &'static str {
        "resource_limits"
    }

    fn handle<'a>(
        &'a self,
        req: Request,
        next: Next,
    ) -> Pin<Box<dyn Future<Output = Response> + Send + 'a>> {
        Box::pin(async move {
            // 1) 请求体上限：优先用 Content-Length 预检（零拷贝拒绝）。
            if let Some(len) = req
                .headers()
                .get(axum::http::header::CONTENT_LENGTH)
                .and_then(|v| v.to_str().ok())
                .and_then(|v| v.parse::<usize>().ok())
            {
                if len > self.max_body_bytes {
                    self.rejected_body.fetch_add(1, Ordering::Relaxed);
                    tracing::warn!(
                        bytes = len,
                        limit = self.max_body_bytes,
                        "request body rejected"
                    );
                    return reject(
                        StatusCode::PAYLOAD_TOO_LARGE,
                        "request_body_too_large",
                        format!("body {len} bytes exceeds limit {}", self.max_body_bytes),
                    );
                }
            }

            // 2) 并发上限：非阻塞获取；占满即拒（不排队——排队会把内存压力换成延迟压力）。
            let is_health = req.uri().path() == "/healthz";
            let permit = if is_health {
                None
            } else {
                match self.max_concurrent.clone().try_acquire_owned() {
                    Ok(p) => Some(p),
                    Err(_) => {
                        self.rejected_concurrency.fetch_add(1, Ordering::Relaxed);
                        tracing::warn!("concurrency limit reached, rejecting");
                        return reject(
                            StatusCode::SERVICE_UNAVAILABLE,
                            "concurrency_limit",
                            "gateway at max concurrent requests",
                        );
                    }
                }
            };

            let resp = next.run(req).await;
            // P2 review：许可必须绑定到响应 body 的生命周期，不能在头返回时释放。
            // 流式代理（MCP/SSE）的 body 在头之后仍在传输；在头返回时 drop 许可
            // 会让活跃的 SSE 连绕不占并发配额。
            let (parts, body) = resp.into_parts();
            let permit_body = PermitBody::wrap(body, permit);
            axum::response::Response::from_parts(parts, permit_body)
        })
    }
}

/// 包装响应 body，持有并发许可直到 body 完成/失败/断开。
struct PermitBody {
    inner: axum::body::Body,
    _permit: Option<tokio::sync::OwnedSemaphorePermit>,
}

impl PermitBody {
    fn wrap(
        inner: axum::body::Body,
        permit: Option<tokio::sync::OwnedSemaphorePermit>,
    ) -> axum::body::Body {
        axum::body::Body::new(Self {
            inner,
            _permit: permit,
        })
    }
}

impl http_body::Body for PermitBody {
    type Data = axum::body::Bytes;
    type Error = axum::Error;

    fn poll_frame(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<http_body::Frame<Self::Data>, Self::Error>>> {
        Pin::new(&mut self.inner).poll_frame(cx)
        // permit 随 self（即 body）的 Drop 释放——body 消费完/连接断开时触发
    }

    fn is_end_stream(&self) -> bool {
        self.inner.is_end_stream()
    }

    fn size_hint(&self) -> http_body::SizeHint {
        self.inner.size_hint()
    }
}
