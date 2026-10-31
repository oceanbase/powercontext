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
            drop(permit); // 显式归还（作用域结束也会归还，这里表达意图）
            resp
        })
    }
}
