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
//! PC_GATEWAY_API_KEY（网关密钥，替代服务商 key）、PC_GATEWAY_CORE_TOKEN、
//! PC_GATEWAY_UPSTREAM_API_KEY、PC_GATEWAY_UPSTREAM_PROFILE、
//! PC_GATEWAY_MAX_ATTEMPTS。

mod authentication;
mod codex_task_outcome_guidance;
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
use pc_gateway_transport::{GatewayTransport, ReqwestTransport};
use std::num::NonZeroU32;
use std::sync::Arc;
use std::time::Duration;

pub struct AppState {
    /// 单一执行器；线格式渲染器按请求格式传入（同格式进出）。
    pub executor: Arc<Executor<CoreLink, GatewayTransport>>,
    pub evidence: Arc<dyn EvidenceSink>,
    pub chat_profile_name: String,
    pub responses_profile_name: Option<String>,
    pub upstream_chat: String,
    pub upstream_responses: String,
    pub upstream_chat_api_key: String,
    pub upstream_responses_api_key: String,
    pub subscription: Option<pc_gateway_auth::SubscriptionAuth>,
    pub api_key: String,
    /// 是否调 prepare 注入上下文（默认关）。
    pub injection: Injection,
    /// Responses 入口是否注入：默认跟随 injection，
    /// `PC_GATEWAY_INJECTION_RESPONSES=off` 可单独关闭。
    pub inject_responses: bool,
    /// 注入时同时请求代码证据：`PC_GATEWAY_INJECTION_CODE=on`（默认关）。
    pub inject_code: bool,
    /// 过检后同步沉淀对话（默认关）。
    pub sediment: bool,
    /// 默认内置固定指令，插入 Responses 和 Chat 请求；文件可覆盖，`PC_GATEWAY_STEERING=off` 可关闭。
    pub steering: Option<String>,
    /// 支持的上游默认保留记忆抽取前缀；`PC_GATEWAY_PREFIX_EXTRACTION=off` 可关闭（#2413）。
    pub prefix_extraction: bool,
    /// 默认 Scope（启动时读一次，避免热路径读环境变量）。
    pub default_scope: String,
    /// Confirmed model input intersection in bytes; zero pauses model routes.
    pub max_body_bytes: usize,
}

#[tokio::main]
async fn main() {
    let arguments: Vec<_> = std::env::args_os().skip(1).collect();
    if arguments.len() == 1 && (arguments[0] == "--version" || arguments[0] == "-V") {
        println!(
            "pc-gateway {} ({}; {})",
            option_env!("PC_GATEWAY_BUILD_VERSION").unwrap_or(env!("CARGO_PKG_VERSION")),
            option_env!("PC_GATEWAY_BUILD_COMMIT").unwrap_or("unknown"),
            option_env!("PC_GATEWAY_BUILD_TARGET").unwrap_or(std::env::consts::ARCH),
        );
        return;
    }
    if arguments.len() == 1 && (arguments[0] == "--help" || arguments[0] == "-h") {
        println!(
            "Usage: pc-gateway [--version | --help | login <copilot|qwen|kimi> <new-credential-file>]\n\
             With no arguments, run the Gateway using PC_GATEWAY_* environment variables.\n\
             Configuration: https://powercontext.oceanbase.io/en/docs/operate/deploy-server/"
        );
        return;
    }
    if !arguments.is_empty() {
        let result = match arguments.as_slice() {
            [command, provider, path] if command == "login" => {
                match provider
                    .to_str()
                    .and_then(|v| pc_gateway_auth::Subscription::parse(v).ok())
                {
                    Some(kind) => match pc_gateway_auth::SubscriptionAuth::new(kind, path.into()) {
                        Ok(auth) => tokio::select! {
                            result = auth.login_device(|uri, code| eprintln!("Open {uri}\nEnter code: {code}")) => result,
                            _ = tokio::signal::ctrl_c() => return,
                        },
                        Err(error) => Err(error),
                    },
                    None => Err(pc_gateway_auth::AuthError::Configuration),
                }
            }
            _ => Err(pc_gateway_auth::AuthError::Configuration),
        };
        if let Err(error) = result {
            eprintln!("{error}; usage: pc-gateway login <copilot|qwen|kimi> <new-credential-file>");
            std::process::exit(2);
        }
        return;
    }
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();

    let (addr, selected_key) = authentication::load().unwrap_or_else(|reason| {
        eprintln!("{reason}");
        std::process::exit(2);
    });
    let api_key = gateway_credentials(selected_key, std::env::var("PC_GATEWAY_CORE_TOKEN").ok())
        .unwrap_or_else(|reason| {
            eprintln!("{reason}");
            std::process::exit(2);
        });

    if [
        "PC_GATEWAY_UPSTREAM_URL",
        "PC_GATEWAY_UPSTREAM_URL_CHAT",
        "PC_GATEWAY_UPSTREAM_URL_RESPONSES",
    ]
    .iter()
    .any(|name| std::env::var_os(name).is_some())
    {
        tracing::error!(
            "Gateway upstream URLs are no longer accepted; configure Core target profiles"
        );
        std::process::exit(2);
    }
    let core_link = CoreLink::from_env();
    let proxy_state = proxy::ProxyState::from_env();
    let subscription = subscription_from_env().unwrap_or_else(|reason| {
        eprintln!("{reason}");
        std::process::exit(2);
    });
    let subscription_kind = subscription.as_ref().map(|auth| auth.kind());
    let (default_chat, default_responses) = subscription_kind
        .map(pc_gateway_auth::Subscription::default_targets)
        .unwrap_or(("", ""));
    let upstream_chat = default_chat.to_string();
    let upstream_responses = default_responses.to_string();

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
    if std::env::var("PC_GATEWAY_DEGRADE").is_ok_and(|value| !value.trim().is_empty()) {
        tracing::error!(
            "PC_GATEWAY_DEGRADE is no longer accepted; remove it and configure Core target profiles"
        );
        std::process::exit(2);
    }

    let steering_enabled = default_on("PC_GATEWAY_STEERING").unwrap_or_else(|reason| {
        eprintln!("{reason}");
        std::process::exit(2);
    });
    let prefix_extraction_requested =
        default_on("PC_GATEWAY_PREFIX_EXTRACTION").unwrap_or_else(|reason| {
            eprintln!("{reason}");
            std::process::exit(2);
        });
    // 显式关闭时不读取指令文件；开启时文件优先于内置文本，配置无效则拒绝启动。
    let steering = match if steering_enabled {
        steering::load(std::env::var("PC_GATEWAY_STEERING_FILE").ok().as_deref())
    } else {
        Ok(None)
    } {
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
    let sediment = matches!(std::env::var("PC_GATEWAY_SEDIMENT").as_deref(), Ok("on"));
    let capture_batch = capabilities
        .as_ref()
        .and_then(|caps| caps.limits.capture_batch_sources)
        .map(|limit| usize::try_from(limit).unwrap_or(usize::MAX));
    if sediment && capabilities.is_some() && capture_batch.is_none() {
        tracing::error!(
            "PC_GATEWAY_SEDIMENT=on, but Core does not offer batch Source capture; turns are not sedimented until Core is upgraded and the gateway restarted"
        );
    }
    if subscription_kind == Some(pc_gateway_auth::Subscription::Claude) {
        eprintln!("Claude CLI has unobservable internal sends and cannot use Core-bound egress");
        std::process::exit(2);
    }
    // 订阅通道里只有 Codex 支持前缀抽取。它的请求重放加密推理，enforcing 的 Core 会暂缓每个抽取请求，
    // 所以只在 Core 确认为非 enforcing 时启用。
    let prefix_extraction = prefix_extraction_requested
        && match subscription_kind {
            None => true,
            Some(pc_gateway_auth::Subscription::Codex) => {
                capabilities.as_ref().is_some_and(|caps| !caps.enforcing)
            }
            Some(_) => false,
        };
    if prefix_extraction_requested && !prefix_extraction {
        tracing::warn!(
            "PC_GATEWAY_PREFIX_EXTRACTION=on is not in effect: with a subscription it needs codex and a Core confirmed as non-enforcing; turns use normal extraction"
        );
    }
    let transport = GatewayTransport::new(
        ReqwestTransport::new(Duration::from_secs(120))
            .with_subscription_responses_sse(matches!(
                subscription_kind,
                Some(pc_gateway_auth::Subscription::Codex | pc_gateway_auth::Subscription::Kimi)
            ))
            .with_codeassist_chat(subscription_kind == Some(pc_gateway_auth::Subscription::Gemini))
            .with_copilot_chat(subscription_kind == Some(pc_gateway_auth::Subscription::Copilot)),
    )
    .with_claude(
        (subscription_kind == Some(pc_gateway_auth::Subscription::Claude))
            .then(|| std::env::var_os("CLAUDE_CLI_EXECUTABLE").unwrap_or_else(|| "claude".into())),
    );

    let state = Arc::new(AppState {
        executor: Arc::new(
            Executor::new(
                core_link.clone(),
                transport,
                NonZeroU32::new(max_attempts).expect("max_attempts > 0"),
            )
            .with_input_limit(model_body_bytes)
            .with_source_token_limit(pc_gateway_core::source_token_limit(
                capabilities
                    .as_ref()
                    .and_then(|caps| caps.limits.atomic_memory_input_tokens),
            ))
            .with_capture_batch_limit(capture_batch)
            .with_core_enforcement(capabilities.as_ref().is_none_or(|caps| caps.enforcing)),
        ),
        evidence,
        chat_profile_name: std::env::var("PC_GATEWAY_UPSTREAM_PROFILE")
            .unwrap_or_else(|_| "default".into()),
        responses_profile_name: std::env::var("PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES")
            .ok()
            .filter(|s| !s.is_empty()),
        upstream_chat,
        upstream_responses,
        subscription,
        upstream_chat_api_key: std::env::var("PC_GATEWAY_UPSTREAM_API_KEY_CHAT")
            .or_else(|_| std::env::var("PC_GATEWAY_UPSTREAM_API_KEY"))
            .unwrap_or_default(),
        upstream_responses_api_key: std::env::var("PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES")
            .or_else(|_| std::env::var("PC_GATEWAY_UPSTREAM_API_KEY"))
            .unwrap_or_default(),
        api_key,
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
        sediment,
        steering,
        prefix_extraction,
        default_scope: std::env::var("PC_GATEWAY_DEFAULT_SCOPE")
            .unwrap_or_else(|_| "gateway:default".into()),
        max_body_bytes: model_body_bytes,
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
    let serving = connection::serve(listener, app, async {
        let deadlines = shutdown.signalled().await;
        executor.close_model_admission();
        deadlines
    })
    .await;
    let totals = executor.turn_counters();
    tracing::info!(
        user_turn = totals.user_turn,
        tool_continuation = totals.tool_continuation,
        assistant_only = totals.assistant_only,
        empty = totals.empty,
        skipped_prepare = totals.skipped_prepare,
        "gateway turn classification totals"
    );
    serving.expect("serve");
}

fn gateway_credentials(
    api_key: Option<String>,
    core_token: Option<String>,
) -> Result<String, &'static str> {
    let api_key = api_key
        .filter(|key| !key.is_empty())
        .ok_or("PC_GATEWAY_API_KEY is required")?;
    let core_token = core_token
        .filter(|token| !token.is_empty())
        .ok_or("PC_GATEWAY_CORE_TOKEN is required")?;
    if !authentication::is_bearer_token(&core_token) {
        return Err("PC_GATEWAY_CORE_TOKEN must be a valid Bearer token");
    }
    if api_key == core_token {
        return Err("PC_GATEWAY_API_KEY must differ from PC_GATEWAY_CORE_TOKEN");
    }
    Ok(api_key)
}

/// Default-on feature switches reject misspellings instead of silently enabling a feature.
fn default_on(name: &str) -> Result<bool, String> {
    match std::env::var(name) {
        Ok(value) if value == "on" => Ok(true),
        Ok(value) if value == "off" => Ok(false),
        Err(std::env::VarError::NotPresent) => Ok(true),
        _ => Err(format!("{name} must be on or off")),
    }
}

fn subscription_from_env(
) -> Result<Option<pc_gateway_auth::SubscriptionAuth>, pc_gateway_auth::AuthError> {
    let value = match std::env::var("PC_GATEWAY_SUBSCRIPTION") {
        Ok(value) => value,
        Err(std::env::VarError::NotPresent) => return Ok(None),
        Err(_) => return Err(pc_gateway_auth::AuthError::Configuration),
    };
    let kind = pc_gateway_auth::Subscription::parse(&value)?;
    if kind == pc_gateway_auth::Subscription::Claude {
        return pc_gateway_auth::SubscriptionAuth::new(kind, "cli-managed".into()).map(Some);
    }
    let path = std::env::var_os("PC_GATEWAY_SUBSCRIPTION_CREDENTIALS")
        .ok_or(pc_gateway_auth::AuthError::Configuration)?;
    pc_gateway_auth::SubscriptionAuth::new(kind, path.into()).map(Some)
}

fn router(state: Arc<AppState>, proxy_state: proxy::ProxyState) -> Router {
    Router::new()
        .route("/healthz", get(healthz))
        .route(
            "/gateway/v1/chat/completions",
            post(ingress::chat_completions),
        )
        .route("/gateway/v1/responses", post(ingress::responses))
        .route("/gateway/v1/extract", post(ingress::extract))
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
    /// 只注入有显式或推导会话 ID 的请求，按回合精确重放准备结果。
    ConversationsOnly,
    /// 不要求会话 ID：每个请求都可注入；用户回合准备，同一回合的后续请求复用该块。
    All,
}

/// `PC_GATEWAY_INJECTION`：`on` 不要求会话 ID 即注入，`off` 关闭。未设置时，同机组合部署（经 UDS 连接已配置的 Core）
/// 默认只注入有显式或推导会话 ID 的请求，不会让工具循环的每一步都重新准备；独立部署默认关闭。
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
    use super::{gateway_credentials, injection_mode, Injection};

    #[test]
    fn gateway_and_core_require_separate_credentials() {
        assert!(gateway_credentials(None, Some("core".into())).is_err());
        assert!(gateway_credentials(Some("gateway".into()), None).is_err());
        assert!(gateway_credentials(Some(String::new()), Some("core".into())).is_err());
        assert!(gateway_credentials(Some("same".into()), Some("same".into())).is_err());
        assert!(gateway_credentials(Some("gateway".into()), Some("core".into())).is_ok());
    }

    #[test]
    fn core_token_must_be_a_valid_bearer_token() {
        for token in [
            "core key",
            "core\r\nx-powercontext-resolve-target: true",
            "core\0key",
        ] {
            assert!(gateway_credentials(Some("gateway".into()), Some(token.into())).is_err());
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
