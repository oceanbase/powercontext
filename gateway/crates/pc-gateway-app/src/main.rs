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

mod authentication;
mod connection;
mod evidence_body;
mod evidence_diagnostics;
mod ingress;
mod link;
mod middleware;
mod proxy;
mod shutdown;
mod steering;

use crate::link::CoreLink;
use axum::routing::{get, post};
use axum::Router;
use pc_gateway_core::Executor;
use pc_gateway_evidence::{EvidenceSink, JsonEvidenceSink, NoopEvidenceSink};
use pc_gateway_transport::ReqwestTransport;
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

pub struct AppState {
    /// 单一执行器；线格式渲染器按请求格式传入（同格式进出）。
    pub executor: Arc<Executor<CoreLink, ReqwestTransport>>,
    pub evidence: Arc<dyn EvidenceSink>,
    pub upstream_profile: String,
    /// chat-completions 上游（含 key；老变量 PC_GATEWAY_UPSTREAM_URL 仍作缺省）。
    pub upstream_chat: String,
    /// responses 上游。
    pub upstream_responses: String,
    pub upstream_chat_api_key: String,
    pub upstream_responses_api_key: String,
    pub api_key: Option<String>,
    pub degrade: pc_gateway_core::DegradePolicy,
    /// 是否调 prepare 注入上下文（默认关）。
    pub injection: Injection,
    /// Responses 入口是否注入：默认跟随 injection，
    /// `PC_GATEWAY_INJECTION_RESPONSES=off` 可单独关闭。
    pub inject_responses: bool,
    /// 注入时同时请求代码证据：`PC_GATEWAY_INJECTION_CODE=on`（默认关）。
    pub inject_code: bool,
    /// 过检后同步沉淀对话（默认关）。
    pub sediment: bool,
    /// 运维配置的固定指令（`PC_GATEWAY_STEERING_FILE`），只插入 Responses 请求；未配置时为空。
    pub steering: Option<String>,
    /// 默认 Scope（启动时读一次，避免热路径读环境变量）。
    pub default_scope: String,
    /// Confirmed model input intersection in bytes; zero pauses model routes.
    pub max_body_bytes: usize,
    /// 启动时查 capabilities 确定降级资格：enforcing=true 或查询失败 → 不允许。
    pub degradation_allowed: bool,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let (addr, api_key) = authentication::load().unwrap_or_else(|reason| {
        eprintln!("{reason}");
        std::process::exit(2);
    });

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

    // 指令文件只在启动时读取；配置了但无效时拒绝启动，而不是不带指令运行。
    let steering = match steering::load(std::env::var("PC_GATEWAY_STEERING_FILE").ok().as_deref()) {
        Ok(steering) => steering,
        Err(reason) => {
            tracing::error!("PC_GATEWAY_STEERING_FILE: {reason}");
            std::process::exit(2);
        }
    };

    // stdout is reserved for body-free evidence JSONL; operational tracing uses stderr.
    let evidence: Arc<dyn EvidenceSink> =
        if matches!(std::env::var("PC_GATEWAY_EVIDENCE").as_deref(), Ok("off")) {
            tracing::info!("gateway evidence disabled; usage and call coverage unavailable");
            Arc::new(NoopEvidenceSink)
        } else {
            match JsonEvidenceSink::new(
                std::io::stdout(),
                std::num::NonZeroUsize::new(1024).unwrap(),
            ) {
                Ok(sink) => {
                    let sink = Arc::new(sink);
                    if evidence_diagnostics::start(&sink).is_err() {
                        tracing::error!(
                            "gateway evidence health reporter unavailable; evidence disabled"
                        );
                        Arc::new(NoopEvidenceSink)
                    } else {
                        tracing::info!(
                            capacity = 1024,
                            "gateway evidence JSONL enabled on stdout; drop newest on overflow"
                        );
                        sink
                    }
                }
                Err(_) => {
                    tracing::error!("gateway evidence worker unavailable; evidence disabled");
                    Arc::new(NoopEvidenceSink)
                }
            }
        };

    // Reuse startup discovery. Fallback keeps its deployment limit; a missing
    // model capacity cannot be treated as permission to send.
    let capabilities = match resolve_core_capabilities(core_link.clone()).await {
        Ok(caps) => Some(caps),
        Err(reason) => {
            // Only fixed local diagnostics: never log Core response/error values.
            tracing::warn!("{reason}; model routes paused until restart");
            None
        }
    };
    let model_body_bytes = capabilities
        .as_ref()
        .and_then(|caps| caps.input_payload_limit())
        .map(|limit| limit.min(max_body_bytes))
        .unwrap_or(0);
    let degradation_allowed = capabilities.as_ref().is_some_and(|caps| !caps.enforcing);

    let state = Arc::new(AppState {
        executor: Arc::new(
            Executor::new(
                core_link.clone(),
                ReqwestTransport::new(Duration::from_secs(120)),
                NonZeroU32::new(max_attempts).expect("max_attempts > 0"),
            )
            .with_input_limit(model_body_bytes),
        ),
        evidence,
        upstream_profile: std::env::var("PC_GATEWAY_UPSTREAM_PROFILE")
            .unwrap_or_else(|_| "default".into()),
        upstream_chat: std::env::var("PC_GATEWAY_UPSTREAM_URL_CHAT")
            .or_else(|_| std::env::var("PC_GATEWAY_UPSTREAM_URL"))
            .unwrap_or_else(|_| "http://127.0.0.1:9000/v1/chat/completions".into()),
        upstream_responses: std::env::var("PC_GATEWAY_UPSTREAM_URL_RESPONSES").unwrap_or_default(),
        upstream_chat_api_key: std::env::var("PC_GATEWAY_UPSTREAM_API_KEY_CHAT")
            .or_else(|_| std::env::var("PC_GATEWAY_UPSTREAM_API_KEY"))
            .unwrap_or_default(),
        upstream_responses_api_key: std::env::var("PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES")
            .or_else(|_| std::env::var("PC_GATEWAY_UPSTREAM_API_KEY"))
            .unwrap_or_default(),
        api_key,
        degrade,
        injection: injection_mode(
            std::env::var("PC_GATEWAY_INJECTION").ok().as_deref(),
            std::env::var("PC_GATEWAY_CORE_UDS").is_ok(),
        ),
        inject_code: matches!(
            std::env::var("PC_GATEWAY_INJECTION_CODE").as_deref(),
            Ok("on")
        ),
        inject_responses: !matches!(
            std::env::var("PC_GATEWAY_INJECTION_RESPONSES").as_deref(),
            Ok("off")
        ),
        sediment: matches!(std::env::var("PC_GATEWAY_SEDIMENT").as_deref(), Ok("on")),
        steering,
        default_scope: std::env::var("PC_GATEWAY_DEFAULT_SCOPE")
            .unwrap_or_else(|_| "gateway:default".into()),
        max_body_bytes: model_body_bytes,
        degradation_allowed,
    });

    let executor = state.executor.clone();
    let shutdown = shutdown::Shutdown::install(executor.model_admission())
        .expect("install process shutdown watchdog before accepting requests");
    let router = router(state, proxy_state);

    // 中间件链：按序注册，前者为外层。新增功能 = 实现 GatewayMiddleware + 此处一行。
    let chain: Vec<std::sync::Arc<dyn middleware::GatewayMiddleware>> = vec![
        middleware::ResourceLimits::new(model_body_bytes, max_body_bytes, max_concurrent),
        std::sync::Arc::new(middleware::RequestId),
    ];
    for mw in &chain {
        tracing::info!(middleware = mw.name(), "middleware enabled");
    }
    let app = middleware::apply_chain(router, chain);

    tracing::info!(%addr, "pc-gateway listening (port takeover mode)");
    let listener = tokio::net::TcpListener::bind(addr).await.expect("bind");
    connection::serve(listener, app, async {
        let deadlines = shutdown.signalled().await;
        executor.close_model_admission();
        deadlines
    })
    .await
    .expect("serve");
}

fn router(state: Arc<AppState>, proxy_state: proxy::ProxyState) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route(
            "/gateway/v1/chat/completions",
            post(ingress::chat_completions),
        )
        .route("/gateway/v1/responses", post(ingress::responses))
        .fallback(proxy::fallback)
        .with_state((state, proxy_state))
}

/// A failed or incompatible startup discovery pauses model routes. Core still
/// checks every send/release; restart the gateway to rediscover changed limits.
async fn resolve_core_capabilities(
    link: link::CoreLink,
) -> Result<pc_gateway_types::ContextProtectionCapabilities, &'static str> {
    use pc_gateway_core::{GateClient, GateError};
    let caps = tokio::time::timeout(Duration::from_secs(10), link.capabilities())
        .await
        .map_err(|_| "Core capabilities query timed out")?
        .map_err(|error| match error {
            GateError::GateUnavailable(_) => "Core capabilities transport unavailable",
            GateError::GateRejected(_) => "Core capabilities query rejected",
            GateError::Malformed(_) => "Core capabilities response malformed",
        })?;
    let caps: pc_gateway_types::ContextProtectionCapabilities = serde_json::from_value(
        caps.get("context_protection")
            .ok_or("Core context_protection capability missing")?
            .clone(),
    )
    .map_err(|_| "Core context_protection DTO incomplete or invalid")?;
    if caps.limits.payload_bytes == 0 {
        return Err("Core input capacity must be positive");
    }
    caps.input_payload_limit()
        .ok_or("Core check/payload versions incompatible")?;
    Ok(caps)
}

async fn healthz() -> axum::Json<serde_json::Value> {
    axum::Json(serde_json::json!({ "status": "ok", "service": "pc-gateway" }))
}

/// 注入范围。
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Injection {
    Off,
    /// 只注入带宿主会话 ID 的请求：一个回合只准备一次并精确重放（RFC 2199）。
    ConversationsOnly,
    /// 所有请求都注入；没有会话 ID 的请求各自准备。
    All,
}

/// `PC_GATEWAY_INJECTION`：`on` 注入所有请求，`off` 关闭。未设置时，同机组合部署（经 UDS 连接已配置的 Core）
/// 默认只注入带会话 ID 的请求，不会让工具循环的每一步都重新准备；独立部署默认关闭。
/// 其他取值按关闭处理并告警，拼写错误不会意外开启注入。
fn injection_mode(setting: Option<&str>, combined: bool) -> Injection {
    match setting {
        Some("on") => Injection::All,
        Some("off") => Injection::Off,
        None if combined => Injection::ConversationsOnly,
        None => Injection::Off,
        Some(_) => {
            tracing::warn!("PC_GATEWAY_INJECTION must be on or off; injection disabled");
            Injection::Off
        }
    }
}

#[cfg(test)]
mod injection_default_tests {
    use super::{injection_mode, Injection};

    #[test]
    fn combined_deployments_inject_conversations_unless_set() {
        assert_eq!(injection_mode(None, true), Injection::ConversationsOnly);
        assert_eq!(injection_mode(Some("on"), true), Injection::All);
        assert_eq!(injection_mode(Some("off"), true), Injection::Off);
    }

    #[test]
    fn standalone_deployments_inject_only_when_turned_on() {
        assert_eq!(injection_mode(None, false), Injection::Off);
        assert_eq!(injection_mode(Some("on"), false), Injection::All);
        assert_eq!(injection_mode(Some("off"), false), Injection::Off);
    }

    #[test]
    fn unknown_values_never_enable_injection() {
        assert_eq!(injection_mode(Some("true"), true), Injection::Off);
        assert_eq!(injection_mode(Some(""), true), Injection::Off);
    }
}
