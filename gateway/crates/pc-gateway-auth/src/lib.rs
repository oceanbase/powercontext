// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

//! Subscription authentication. Credentials never enter model bodies or Core check payloads.
//! Grok's file format follows republic; Codex CLI credentials remain CLI-owned and read-only.

use fs2::FileExt;
use oauth2::{basic::BasicClient, AuthType, ClientId, RefreshToken, TokenResponse, TokenUrl};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    time::Duration,
};

mod gemini;
mod owned;

pub const GROK_ISSUER: &str = "https://auth.x.ai";
pub const GROK_CLIENT_ID: &str = "b1a00492-073a-47ea-816f-4c329264a828";
pub const GROK_SCOPE: &str = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828";
const STORE_LIMIT: u64 = 1024 * 1024;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Subscription {
    Grok,
    Codex,
    Copilot,
    Qwen,
    Kimi,
    Gemini,
    Claude,
}

impl Subscription {
    pub fn parse(value: &str) -> Result<Self, AuthError> {
        match value {
            "grok" => Ok(Self::Grok),
            "codex" => Ok(Self::Codex),
            "copilot" => Ok(Self::Copilot),
            "qwen" => Ok(Self::Qwen),
            "kimi" => Ok(Self::Kimi),
            "gemini" => Ok(Self::Gemini),
            "claude" => Ok(Self::Claude),
            _ => Err(AuthError::Configuration),
        }
    }

    /// Exact endpoint allowlist; a valid token is never permission to choose another target.
    pub fn validate_target(self, target: &str) -> Result<(), AuthError> {
        if self == Self::Claude {
            return (target == "claude-cli://execute")
                .then_some(())
                .ok_or(AuthError::Target);
        }
        let url = reqwest::Url::parse(target).map_err(|_| AuthError::Target)?;
        if url.scheme() != "https"
            || !url.username().is_empty()
            || url.password().is_some()
            || url.port().is_some_and(|port| port != 443)
            || url.query().is_some()
            || url.fragment().is_some()
        {
            return Err(AuthError::Target);
        }
        let allowed = match self {
            Self::Grok => {
                url.host_str() == Some("api.x.ai")
                    && matches!(url.path(), "/v1/chat/completions" | "/v1/responses")
            }
            Self::Codex => {
                url.host_str() == Some("chatgpt.com")
                    && url.path() == "/backend-api/codex/responses"
            }
            Self::Copilot => {
                url.host_str() == Some("api.githubcopilot.com")
                    && matches!(url.path(), "/chat/completions" | "/responses")
            }
            Self::Qwen => {
                url.host_str() == Some("api.qwen.ai")
                    && url.path() == "/compatible-mode/v1/chat/completions"
            }
            Self::Kimi => {
                matches!(url.host_str(), Some("api.kimi.com" | "api.kimi.ai"))
                    && matches!(
                        url.path(),
                        "/coding/v1/chat/completions" | "/coding/v1/responses"
                    )
            }
            Self::Gemini => {
                url.host_str() == Some("cloudcode-pa.googleapis.com")
                    && url.path() == "/v1internal:generateContent"
            }
            Self::Claude => unreachable!(),
        };
        allowed.then_some(()).ok_or(AuthError::Target)
    }
}

/// Fixed, content-free errors: no provider response, secret, file path, or chained HTTP error.
#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    #[error("invalid subscription configuration")]
    Configuration,
    #[error("subscription destination is not allowed")]
    Target,
    #[error("cannot read subscription credentials; sign in with the provider CLI")]
    Credentials,
    #[error("cannot refresh subscription credentials; sign in again")]
    Refresh,
    #[error("cannot update subscription credentials")]
    Store,
}

/// This value intentionally does not implement Debug or Serialize.
pub struct SubscriptionAuth {
    kind: Subscription,
    path: PathBuf,
    client: reqwest::Client,
    session: tokio::sync::Mutex<Option<CopilotSession>>,
    token_url: String,
    device_url: String,
    codeassist_url: String,
    codeassist_project: Option<String>,
}

struct CopilotSession {
    github_token: String,
    token: String,
    expires_at: i64,
}

impl SubscriptionAuth {
    pub fn new(kind: Subscription, credentials: PathBuf) -> Result<Self, AuthError> {
        if credentials.as_os_str().is_empty() {
            return Err(AuthError::Configuration);
        }
        let client = reqwest::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .retry(reqwest::retry::never())
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(|_| AuthError::Configuration)?;
        Ok(Self {
            kind,
            path: credentials,
            client,
            session: tokio::sync::Mutex::new(None),
            token_url: match kind {
                Subscription::Grok => "https://auth.x.ai/oauth2/token",
                Subscription::Copilot => "https://api.github.com/copilot_internal/v2/token",
                Subscription::Codex => "",
                Subscription::Qwen => "https://auth.qwen.ai/api/oauth/token",
                Subscription::Kimi => "https://auth.kimi.com/api/oauth/token",
                Subscription::Gemini | Subscription::Claude => "",
            }
            .into(),
            device_url: match kind {
                Subscription::Qwen => "https://auth.qwen.ai/api/oauth/device/code",
                Subscription::Kimi => "https://auth.kimi.com/api/oauth/device_authorization",
                Subscription::Copilot => "https://github.com/login/device/code",
                _ => "",
            }
            .into(),
            codeassist_url: "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist".into(),
            codeassist_project: std::env::var("GOOGLE_CLOUD_PROJECT")
                .or_else(|_| std::env::var("GOOGLE_CLOUD_PROJECT_ID"))
                .ok(),
        })
    }

    pub fn kind(&self) -> Subscription {
        self.kind
    }

    /// Validate before file I/O and renewal. Dropping this future cancels token I/O and releases
    /// its OS lock; no detached refresh or persistence task outlives the caller.
    pub async fn headers(
        &self,
        target: &str,
        now: i64,
    ) -> Result<Vec<(String, String)>, AuthError> {
        self.kind.validate_target(target)?;
        match self.kind {
            Subscription::Codex => {
                let data = read_store(&self.path)?;
                if data
                    .get("auth_mode")
                    .and_then(Value::as_str)
                    .is_some_and(|v| v != "chatgpt")
                {
                    return Err(AuthError::Credentials);
                }
                let token = text(&data["tokens"], "access_token")?;
                let account = text(&data["tokens"], "account_id")?;
                Ok(vec![
                    bearer(token)?,
                    checked_header("chatgpt-account-id", account)?,
                ])
            }
            Subscription::Grok => {
                let data = read_store(&self.path)?;
                let stored = grok_token(&data)?;
                if expiry(stored)? > now.saturating_add(60) {
                    return Ok(vec![bearer(text(stored, "key")?)?]);
                }
                let _lock = tokio::time::timeout(Duration::from_secs(30), lock_store(&self.path))
                    .await
                    .map_err(|_| AuthError::Store)??;
                // Another process or instance may have rotated the refresh token while we waited.
                let mut data = read_store(&self.path)?;
                let stored = grok_token(&data)?;
                if expiry(stored)? > now.saturating_add(60) {
                    return Ok(vec![bearer(text(stored, "key")?)?]);
                }
                let refresh = text(stored, "refresh_token")?;
                let client = BasicClient::new(ClientId::new(GROK_CLIENT_ID.into()))
                    .set_auth_type(AuthType::RequestBody)
                    .set_token_uri(
                        TokenUrl::new(self.token_url.clone()).map_err(|_| AuthError::Refresh)?,
                    );
                let refresh_token = RefreshToken::new(refresh.into());
                let mut exchange = client.exchange_refresh_token(&refresh_token);
                for key in ["principal_type", "principal_id"] {
                    if let Some(value) = stored.get(key) {
                        let value = value
                            .as_str()
                            .filter(|v| !v.is_empty())
                            .ok_or(AuthError::Credentials)?;
                        exchange = exchange.add_extra_param(key, value);
                    }
                }
                let http = TokenHttp {
                    client: self.client.clone(),
                    default_bearer: true,
                    headers: Vec::new(),
                };
                let token = exchange
                    .request_async(&http)
                    .await
                    .map_err(|_| AuthError::Refresh)?;
                if !token
                    .token_type()
                    .eq(&oauth2::basic::BasicTokenType::Bearer)
                {
                    return Err(AuthError::Refresh);
                }
                let access = token.access_token().secret();
                bearer(access).map_err(|_| AuthError::Refresh)?;
                let seconds = token.expires_in().ok_or(AuthError::Refresh)?.as_secs();
                let seconds = i64::try_from(seconds).map_err(|_| AuthError::Refresh)?;
                if seconds <= 0 {
                    return Err(AuthError::Refresh);
                }
                let expires_at = now.checked_add(seconds).ok_or(AuthError::Refresh)?;
                let date =
                    chrono::DateTime::from_timestamp(expires_at, 0).ok_or(AuthError::Refresh)?;
                let rotated = token
                    .refresh_token()
                    .map(|v| v.secret().as_str())
                    .unwrap_or(refresh);
                if rotated.is_empty() {
                    return Err(AuthError::Refresh);
                }
                let updated = serde_json::json!({
                    "key": access, "refresh_token": rotated, "expires_at": date.to_rfc3339(),
                });
                data[GROK_SCOPE]
                    .as_object_mut()
                    .ok_or(AuthError::Credentials)?
                    .extend(updated.as_object().unwrap().clone());
                // No await between receiving a rotated credential and committing it. The caller
                // cannot cancel half an atomic replacement; pre-response cancellation writes nothing.
                write_store(&self.path, &data)?;
                Ok(vec![bearer(access)?])
            }
            Subscription::Copilot => self.copilot_headers(now).await,
            Subscription::Qwen | Subscription::Kimi => self.owned_headers(now).await,
            Subscription::Gemini => self.gemini_context(target, now).await.map(|value| value.0),
            Subscription::Claude => Ok(Vec::new()),
        }
    }

    async fn copilot_headers(&self, now: i64) -> Result<Vec<(String, String)>, AuthError> {
        let data = read_store(&self.path)?;
        let github = text(&data, "oauth_token")?;
        let mut session = self.session.lock().await;
        if let Some(current) = session
            .as_ref()
            .filter(|s| s.github_token == github && s.expires_at > now.saturating_add(120))
        {
            return copilot_headers(&current.token);
        }
        // This GitHub-specific session exchange is not an RFC 6749 token grant.
        let mut request = self
            .client
            .get(&self.token_url)
            .header(
                "authorization",
                checked_header("authorization", &format!("token {github}"))?.1,
            )
            .header("accept", "application/json");
        for (name, value) in copilot_metadata() {
            request = request.header(name, value);
        }
        let response = request.send().await.map_err(|_| AuthError::Refresh)?;
        if response.status() != reqwest::StatusCode::OK {
            return Err(AuthError::Refresh);
        }
        let mut response = response;
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| AuthError::Refresh)? {
            if chunk.len() > (STORE_LIMIT as usize).saturating_sub(bytes.len()) {
                return Err(AuthError::Refresh);
            }
            bytes.extend_from_slice(&chunk);
        }
        let body: Value = serde_json::from_slice(&bytes).map_err(|_| AuthError::Refresh)?;
        let token = text(&body, "token").map_err(|_| AuthError::Refresh)?;
        let expires_at = body["expires_at"]
            .as_i64()
            .filter(|v| *v > now.saturating_add(120))
            .ok_or(AuthError::Refresh)?;
        // Never trust an arbitrary endpoint from a token response as a credential destination.
        if body
            .pointer("/endpoints/api")
            .and_then(Value::as_str)
            .is_some_and(|v| v.trim_end_matches('/') != "https://api.githubcopilot.com")
        {
            return Err(AuthError::Target);
        }
        let headers = copilot_headers(token).map_err(|_| AuthError::Refresh)?;
        *session = Some(CopilotSession {
            github_token: github.into(),
            token: token.into(),
            expires_at,
        });
        Ok(headers)
    }
}

fn copilot_headers(token: &str) -> Result<Vec<(String, String)>, AuthError> {
    let mut headers = copilot_metadata();
    headers.push(bearer(token)?);
    Ok(headers)
}

fn copilot_metadata() -> Vec<(String, String)> {
    // Editor compatibility fields follow the established Copilot HTTP ecosystem. The
    // product User-Agent remains PowerContext; real account/model acceptance is separate.
    vec![
        ("editor-version".into(), "vscode/1.140.0".into()),
        ("editor-plugin-version".into(), "copilot-chat/0.68.0".into()),
        ("copilot-integration-id".into(), "vscode-chat".into()),
        (
            "user-agent".into(),
            concat!("PowerContextGateway/", env!("CARGO_PKG_VERSION")).into(),
        ),
        ("x-github-api-version".into(), "2026-01-09".into()),
    ]
}

fn checked_header(name: &str, value: &str) -> Result<(String, String), AuthError> {
    if value.is_empty() || reqwest::header::HeaderValue::from_str(value).is_err() {
        return Err(AuthError::Credentials);
    }
    Ok((name.into(), value.into()))
}

fn bearer(token: &str) -> Result<(String, String), AuthError> {
    if token.is_empty() || !token.bytes().all(|byte| byte.is_ascii_graphic()) {
        return Err(AuthError::Credentials);
    }
    checked_header("authorization", &format!("Bearer {token}"))
}

fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, AuthError> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty())
        .ok_or(AuthError::Credentials)
}

fn grok_token(data: &Value) -> Result<&Value, AuthError> {
    let token = &data[GROK_SCOPE];
    if text(token, "oidc_issuer")? != GROK_ISSUER
        || text(token, "oidc_client_id")? != GROK_CLIENT_ID
    {
        return Err(AuthError::Credentials);
    }
    bearer(text(token, "key")?)?;
    expiry(token)?;
    Ok(token)
}

fn expiry(token: &Value) -> Result<i64, AuthError> {
    let value = &token["expires_at"];
    if let Some(seconds) = value.as_i64() {
        return Ok(seconds);
    }
    chrono::DateTime::parse_from_rfc3339(value.as_str().ok_or(AuthError::Credentials)?)
        .map(|v| v.timestamp())
        .map_err(|_| AuthError::Credentials)
}

fn read_store(path: &Path) -> Result<Value, AuthError> {
    let file = File::open(path).map_err(|_| AuthError::Credentials)?;
    let mut bytes = Vec::new();
    file.take(STORE_LIMIT + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| AuthError::Credentials)?;
    if bytes.len() as u64 > STORE_LIMIT {
        return Err(AuthError::Credentials);
    }
    serde_json::from_slice(&bytes).map_err(|_| AuthError::Credentials)
}

async fn lock_store(path: &Path) -> Result<File, AuthError> {
    let lock_path = path.with_file_name("auth.json.lock");
    lock_file(&lock_path).await
}

async fn lock_file(lock_path: &Path) -> Result<File, AuthError> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options.open(lock_path).map_err(|_| AuthError::Store)?;
    loop {
        match file.try_lock_exclusive() {
            Ok(()) => return Ok(file),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            Err(_) => return Err(AuthError::Store),
        }
    }
}

fn write_store(path: &Path, data: &Value) -> Result<(), AuthError> {
    persist_store(path, data, true)
}

fn persist_store(path: &Path, data: &Value, replace: bool) -> Result<(), AuthError> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|_| AuthError::Store)?;
    serde_json::to_writer(file.as_file_mut(), data).map_err(|_| AuthError::Store)?;
    file.write_all(b"\n").map_err(|_| AuthError::Store)?;
    file.as_file().sync_all().map_err(|_| AuthError::Store)?;
    if replace {
        file.persist(path).map_err(|_| AuthError::Store)?;
    } else {
        file.persist_noclobber(path).map_err(|_| AuthError::Store)?;
    }
    #[cfg(unix)]
    File::open(parent)
        .and_then(|f| f.sync_all())
        .map_err(|_| AuthError::Store)?;
    Ok(())
}

#[cfg(test)]
mod tests;

struct TokenHttp {
    client: reqwest::Client,
    default_bearer: bool,
    headers: Vec<(String, String)>,
}

impl TokenHttp {
    async fn request(
        &self,
        request: oauth2::HttpRequest,
    ) -> Result<oauth2::HttpResponse, AuthError> {
        let mut request: reqwest::Request = request.try_into().map_err(|_| AuthError::Refresh)?;
        for (name, value) in &self.headers {
            request.headers_mut().insert(
                reqwest::header::HeaderName::from_bytes(name.as_bytes())
                    .map_err(|_| AuthError::Configuration)?,
                reqwest::header::HeaderValue::from_str(value)
                    .map_err(|_| AuthError::Configuration)?,
            );
        }
        let mut response = self
            .client
            .execute(request)
            .await
            .map_err(|_| AuthError::Refresh)?;
        let mut builder = oauth2::http::Response::builder().status(response.status());
        for (name, value) in response.headers() {
            builder = builder.header(name, value);
        }
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| AuthError::Refresh)? {
            if chunk.len() > (STORE_LIMIT as usize).saturating_sub(bytes.len()) {
                return Err(AuthError::Refresh);
            }
            bytes.extend_from_slice(&chunk);
        }
        // xAI's response may omit token_type. OAuth parsing and grant validation stay in oauth2;
        // the provider adapter supplies the Bearer default, as republic's Grok adapter does.
        if response.status().is_success() && self.default_bearer {
            if let Ok(Value::Object(mut value)) = serde_json::from_slice::<Value>(&bytes) {
                value
                    .entry("token_type")
                    .or_insert_with(|| Value::String("Bearer".into()));
                bytes = serde_json::to_vec(&value).map_err(|_| AuthError::Refresh)?;
            }
        }
        builder.body(bytes).map_err(|_| AuthError::Refresh)
    }
}

impl<'c> oauth2::AsyncHttpClient<'c> for TokenHttp {
    type Error = AuthError;
    type Future = std::pin::Pin<
        Box<
            dyn std::future::Future<Output = Result<oauth2::HttpResponse, Self::Error>>
                + Send
                + Sync
                + 'c,
        >,
    >;

    fn call(&'c self, request: oauth2::HttpRequest) -> Self::Future {
        Box::pin(self.request(request))
    }
}
