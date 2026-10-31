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

//! 薄二进制：端口接管 + 模型入口。所有执行逻辑在 `pc-gateway-core`。
//!
//! PR #2199 部署形态：
//! - 同机组合：`PC_GATEWAY_CORE_UDS` 指向 Python 的 UDS；网关独占公网端口，
//!   非 `/gateway/*` 流量反向代理到 UDS（原 Core/MCP 路由透传）。
//! - 独立运行：`PC_GATEWAY_CORE_BASE` 直连远端 PowerContext（无代理）。
//!
//! 环境变量：PC_GATEWAY_LISTEN、PC_GATEWAY_CORE_UDS、PC_GATEWAY_CORE_BASE、
//! PC_GATEWAY_API_KEY（网关密钥，替代服务商 key）、PC_GATEWAY_UPSTREAM_URL、
//! PC_GATEWAY_UPSTREAM_API_KEY、PC_GATEWAY_UPSTREAM_PROFILE、
//! PC_GATEWAY_POLICY_VERSION、PC_GATEWAY_MAX_ATTEMPTS、PC_GATEWAY_DEGRADE。

mod ingress;
mod link;
mod middleware;
mod proxy;

use crate::link::CoreLink;
use axum::routing::{get, post};
use axum::Router;
use pc_gateway_core::Executor;
use pc_gateway_evidence::BufferedEvidenceSink;
use pc_gateway_transport::ReqwestTransport;
use std::net::SocketAddr;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

pub struct AppState {
    /// 单一执行器；线格式渲染器按请求格式传入（同格式进出）。
    pub executor: Arc<Executor<CoreLink, ReqwestTransport>>,
    pub evidence: Arc<BufferedEvidenceSink>,
    pub upstream_profile: String,
    /// chat-completions 上游（含 key；老变量 PC_GATEWAY_UPSTREAM_URL 仍作缺省）。
    pub upstream_chat: String,
    /// responses 上游。
    pub upstream_responses: String,
    pub upstream_chat_api_key: String,
    pub upstream_responses_api_key: String,
    pub api_key: Option<String>,
    pub degrade: pc_gateway_core::DegradePolicy,
    /// 是否调 prepare 注入上下文（默认关；v1 仅 chat 格式）。
    pub inject_context: bool,
    /// 过检后同步沉淀对话（默认关）。
    pub sediment: bool,
    /// 默认 Scope（启动时读一次，避免热路径读环境变量）。
    pub default_scope: String,
    /// Request body byte limit (also enforced when consuming chunked bodies).
    pub max_body_bytes: usize,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let core_link = CoreLink::from_env();
    let proxy_state = proxy::ProxyState::from_env();

    let max_attempts: u32 = std::env::var("PC_GATEWAY_MAX_ATTEMPTS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(2);
    let max_body_bytes: usize = std::env::var("PC_GATEWAY_MAX_BODY_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(16 * 1024 * 1024);
    let max_concurrent: usize = std::env::var("PC_GATEWAY_MAX_CONCURRENT")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|v| *v > 0)
        .unwrap_or(64);
    let degrade = match std::env::var("PC_GATEWAY_DEGRADE").as_deref() {
        Ok("passthrough") => pc_gateway_core::DegradePolicy::PassthroughWithoutMemory,
        _ => pc_gateway_core::DegradePolicy::Pause,
    };

    let state = Arc::new(AppState {
        executor: Arc::new(Executor::new(
            core_link,
            ReqwestTransport::new(Duration::from_secs(120)),
            NonZeroU32::new(max_attempts).expect("max_attempts > 0"),
        )),
        evidence: Arc::new(BufferedEvidenceSink::new()),
        upstream_profile: std::env::var("PC_GATEWAY_UPSTREAM_PROFILE")
            .unwrap_or_else(|_| "default".into()),
        upstream_chat: std::env::var("PC_GATEWAY_UPSTREAM_URL_CHAT")
            .or_else(|_| std::env::var("PC_GATEWAY_UPSTREAM_URL"))
            .unwrap_or_else(|_| "http://127.0.0.1:9000/v1/chat/completions".into()),
        upstream_responses: std::env::var("PC_GATEWAY_UPSTREAM_URL_RESPONSES")
            .unwrap_or_else(|_| "https://openrouter.ai/api/v1/responses".into()),
        upstream_chat_api_key: std::env::var("PC_GATEWAY_UPSTREAM_API_KEY_CHAT")
            .or_else(|_| std::env::var("PC_GATEWAY_UPSTREAM_API_KEY"))
            .unwrap_or_default(),
        upstream_responses_api_key: std::env::var("PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES")
            .or_else(|_| std::env::var("PC_GATEWAY_UPSTREAM_API_KEY"))
            .unwrap_or_default(),
        api_key: std::env::var("PC_GATEWAY_API_KEY")
            .ok()
            .filter(|k| !k.is_empty()),
        degrade,
        inject_context: matches!(std::env::var("PC_GATEWAY_INJECTION").as_deref(), Ok("on")),
        sediment: matches!(std::env::var("PC_GATEWAY_SEDIMENT").as_deref(), Ok("on")),
        default_scope: std::env::var("PC_GATEWAY_DEFAULT_SCOPE")
            .unwrap_or_else(|_| "gateway:default".into()),
        max_body_bytes,
    });

    let router = Router::new()
        .route("/healthz", get(healthz))
        .route(
            "/gateway/v1/chat/completions",
            post(ingress::chat_completions),
        )
        .route("/gateway/v1/responses", post(ingress::responses))
        .fallback(proxy::fallback)
        .with_state((state, proxy_state));

    // 中间件链：按序注册，前者为外层。新增功能 = 实现 GatewayMiddleware + 此处一行。
    let chain: Vec<std::sync::Arc<dyn middleware::GatewayMiddleware>> = vec![
        middleware::ResourceLimits::new(max_body_bytes, max_concurrent),
        std::sync::Arc::new(middleware::RequestId),
    ];
    for mw in &chain {
        tracing::info!(middleware = mw.name(), "middleware enabled");
    }
    let app = middleware::apply_chain(router, chain);

    let addr: SocketAddr = std::env::var("PC_GATEWAY_LISTEN")
        .unwrap_or_else(|_| "127.0.0.1:8787".into())
        .parse()
        .expect("PC_GATEWAY_LISTEN must be host:port");

    tracing::info!(%addr, "pc-gateway listening (port takeover mode)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
        .expect("serve");
}

async fn shutdown_signal() {
    let _ = tokio::signal::ctrl_c().await;
    tracing::info!("shutting down (draining)");
}

async fn healthz() -> axum::Json<serde_json::Value> {
    axum::Json(serde_json::json!({ "status": "ok", "service": "pc-gateway" }))
}
