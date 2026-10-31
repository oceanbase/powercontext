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

//! 网关中间件链（chain/filter）。
//!
//! 设计对齐 PC 的 HttpResourceLimits / HttpRequestId / 观测栈分层思路：
//! 每个功能是一个实现 `GatewayMiddleware` 的小结构，启动时按序注册，
//! `apply_chain` 反向挂到 axum（先注册 = 越靠外层）。新增功能（限流、
//! 指标、OTel、熔断……）只需增加一个结构体 + 一行注册。

use axum::{
    extract::Request,
    http::{HeaderName, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
    Json,
};
use std::pin::Pin;

use std::future::Future;
use std::sync::Arc;

pub mod request_id;
pub use request_id::RequestId;
pub mod resource_limits;
pub use resource_limits::ResourceLimits;

pub const REQUEST_ID_HEADER: HeaderName = HeaderName::from_static("x-powercontext-request-id");

/// 一个中间件 = 请求进入时的一段处理 + 调用后续链。
/// 约定：中间件不得消费 body（body 归 ingress/代理所有）。
/// handle 返回 boxed future 以保持 dyn 兼容（可装进 Vec 动态组合）。
pub trait GatewayMiddleware: Send + Sync + 'static {
    /// 名称（日志/指标用）。
    fn name(&self) -> &'static str;

    fn handle<'a>(
        &'a self,
        req: Request,
        next: Next,
    ) -> Pin<Box<dyn Future<Output = Response> + Send + 'a>>;
}

/// 把中间件链挂到 Router 的外层。
/// axum 的 layer 语义：后挂的先执行；这里让 `chain[0]` 成为最外层，
/// 与注册顺序直觉一致。
pub fn apply_chain(
    mut router: axum::Router,
    chain: Vec<Arc<dyn GatewayMiddleware>>,
) -> axum::Router {
    for mw in chain.into_iter().rev() {
        router = router.layer(axum::middleware::from_fn(move |req, next| {
            let mw = mw.clone();
            async move { mw.handle(req, next).await }
        }));
    }
    router
}

/// 统一的简单错误响应。
pub(crate) fn reject(status: StatusCode, code: &str, detail: impl Into<String>) -> Response {
    (
        status,
        Json(serde_json::json!({ "error": code, "detail": detail.into() })),
    )
        .into_response()
}
