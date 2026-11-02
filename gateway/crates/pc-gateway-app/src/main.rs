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
mod steering;

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
    /// 是否调 prepare 注入上下文（默认关）。
    pub injection: Injection,
    /// Responses 入口是否注入：默认跟随 injection，
    /// `PC_GATEWAY_INJECTION_RESPONSES=off` 可单独关闭。
    pub inject_responses: bool,
    /// 过检后同步沉淀对话（默认关）。
    pub sediment: bool,
    /// 运维配置的固定指令（`PC_GATEWAY_STEERING_FILE`），只插入 Responses 请求；未配置时为空。
    pub steering: Option<String>,
    /// 客户端没有设置输出上限时使用的默认值（`PC_GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS`）；未配置时不补。
    pub default_max_output_tokens: Option<u32>,
    /// 默认 Scope（启动时读一次，避免热路径读环境变量）。
    pub default_scope: String,
    /// Request body byte limit (also enforced when consuming chunked bodies).
    pub max_body_bytes: usize,
    /// 启动时查 capabilities 确定降级资格：enforcing=true 或查询失败 → 不允许。
    pub degradation_allowed: bool,
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

    // 指令文件只在启动时读取；配置了但无效时拒绝启动，而不是不带指令运行。
    let steering = match steering::load(std::env::var("PC_GATEWAY_STEERING_FILE").ok().as_deref()) {
        Ok(steering) => steering,
        Err(reason) => {
            tracing::error!("PC_GATEWAY_STEERING_FILE: {reason}");
            std::process::exit(2);
        }
    };

    let default_max_output_tokens = match default_output_limit(
        std::env::var("PC_GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS")
            .ok()
            .as_deref(),
    ) {
        Ok(limit) => limit,
        Err(reason) => {
            tracing::error!("PC_GATEWAY_DEFAULT_MAX_OUTPUT_TOKENS: {reason}");
            std::process::exit(2);
        }
    };

    let state = Arc::new(AppState {
        executor: Arc::new(Executor::new(
            core_link.clone(),
            ReqwestTransport::new(Duration::from_secs(120)),
            NonZeroU32::new(max_attempts).expect("max_attempts > 0"),
        )),
        evidence: Arc::new(BufferedEvidenceSink::new()),
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
        api_key: std::env::var("PC_GATEWAY_API_KEY")
            .ok()
            .filter(|k| !k.is_empty()),
        degrade,
        injection: injection_mode(
            std::env::var("PC_GATEWAY_INJECTION").ok().as_deref(),
            std::env::var("PC_GATEWAY_CORE_UDS").is_ok(),
        ),
        inject_responses: !matches!(
            std::env::var("PC_GATEWAY_INJECTION_RESPONSES").as_deref(),
            Ok("off")
        ),
        sediment: matches!(std::env::var("PC_GATEWAY_SEDIMENT").as_deref(), Ok("on")),
        steering,
        default_max_output_tokens,
        default_scope: std::env::var("PC_GATEWAY_DEFAULT_SCOPE")
            .unwrap_or_else(|_| "gateway:default".into()),
        max_body_bytes,
        degradation_allowed: resolve_degradation(core_link.clone()).await,
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

/// P1-3：启动时确定降级资格。
/// capabilities 返回 enforcing=true → 不允许（Core 说了算）。
/// 查询失败 → 不允许（fail-closed）。
async fn resolve_degradation(link: link::CoreLink) -> bool {
    use pc_gateway_core::GateClient;
    match tokio::time::timeout(std::time::Duration::from_secs(10), link.capabilities()).await {
        Ok(Ok(caps)) => {
            let cp = caps.get("context_protection");
            // P2 review：验契约版本——Core 必须支持我们消费的 check/payload 契约。
            let check_ok = cp
                .and_then(|cp| cp.get("check_versions"))
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .any(|s| s.as_str() == Some("powercontext.context-check.v1"))
                })
                .unwrap_or(false);
            let payload_ok = cp
                .and_then(|cp| cp.get("payload_versions"))
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .any(|s| s.as_str() == Some("powercontext.model-json-payload.v1"))
                })
                .unwrap_or(false);
            if !check_ok || !payload_ok {
                tracing::error!(
                    check_ok,
                    payload_ok,
                    "Core does not support the required context-check contract; \
                     this gateway requires #2311+#2313. Startup will continue but \
                     every /gateway/v1 request will fail until the contract is available."
                );
                return false; // 降级也不允许——没有 check 契约就没有安全保障
            }
            let enforcing = cp
                .and_then(|cp| cp.get("enforcing"))
                .and_then(|e| e.as_bool())
                .unwrap_or(true); // 缺字段 → fail-closed
            let allowed = !enforcing;
            tracing::info!(
                enforcing,
                allowed,
                "degradation eligibility from capabilities"
            );
            allowed
        }
        Ok(Err(e)) => {
            tracing::warn!(error = %e, "capabilities error; degradation disabled (fail-closed)");
            false
        }
        Err(_) => {
            tracing::warn!("capabilities timeout (10s); degradation disabled (fail-closed)");
            false
        }
    }
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
/// 默认输出上限只在启动时解析：须为正整数；配置了但无效时拒绝启动，而不是不补上限地运行。
fn default_output_limit(setting: Option<&str>) -> Result<Option<u32>, String> {
    let Some(value) = setting else {
        return Ok(None);
    };
    match value.trim().parse::<u32>() {
        Ok(limit) if limit > 0 => Ok(Some(limit)),
        _ => Err("must be a positive integer".into()),
    }
}

fn injection_mode(setting: Option<&str>, combined: bool) -> Injection {
    match setting {
        Some("on") => Injection::All,
        Some("off") => Injection::Off,
        None if combined => Injection::ConversationsOnly,
        None => Injection::Off,
        Some(other) => {
            tracing::warn!(
                value = other,
                "PC_GATEWAY_INJECTION must be on or off; injection disabled"
            );
            Injection::Off
        }
    }
}

#[cfg(test)]
mod injection_default_tests {
    use super::{injection_mode, Injection};

    #[test]
    fn the_default_output_limit_is_a_positive_integer_or_absent() {
        use super::default_output_limit;
        assert_eq!(default_output_limit(None), Ok(None));
        assert_eq!(default_output_limit(Some("32000")), Ok(Some(32000)));
        for invalid in ["", "0", "-1", "32k", "4294967296"] {
            assert!(default_output_limit(Some(invalid)).is_err(), "{invalid}");
        }
    }

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
