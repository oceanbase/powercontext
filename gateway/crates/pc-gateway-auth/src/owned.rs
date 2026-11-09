// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
// Provider endpoints and wire behavior follow Qwen Code and Kimi Code; see LICENSES/oauth.md.

use super::*;
use oauth2::{
    DeviceAuthorizationResponse, DeviceAuthorizationUrl, EmptyExtraDeviceAuthorizationFields, Scope,
};

const OWNER: &str = "powercontext.gateway.oauth.v1";

impl Subscription {
    fn device_client_id(self) -> Result<&'static str, AuthError> {
        match self {
            Self::Qwen => Ok("qwen-code-cli"),
            Self::Kimi => Ok("17e5f671-d194-4dfb-9706-5516cb48c098"),
            Self::Copilot => Ok("Iv1.b507a08c87ecfe98"),
            _ => Err(AuthError::Configuration),
        }
    }
}

fn now_seconds() -> Result<i64, AuthError> {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_secs() as i64)
        .map_err(|_| AuthError::Configuration)
}

fn identity(kind: Subscription, data: &Value) -> Result<Vec<(String, String)>, AuthError> {
    let mut headers = vec![(
        "user-agent".into(),
        format!("PowerContextGateway/{}", env!("CARGO_PKG_VERSION")),
    )];
    if kind == Subscription::Kimi {
        for (name, value) in [
            ("x-msh-platform", "kimi_cli"),
            ("x-msh-version", env!("CARGO_PKG_VERSION")),
            ("x-msh-device-name", "powercontext-gateway"),
            ("x-msh-device-model", std::env::consts::ARCH),
            ("x-msh-os-version", std::env::consts::OS),
            ("x-msh-device-id", text(data, "device_id")?),
        ] {
            headers.push(checked_header(name, value)?);
        }
    }
    Ok(headers)
}

fn validate_owned(data: &Value, kind: Subscription) -> Result<(), AuthError> {
    if data["schema"] != OWNER || data["subscription"] != serde_json::to_value(kind).unwrap() {
        return Err(AuthError::Credentials);
    }
    if data["token_type"] != "Bearer" {
        return Err(AuthError::Credentials);
    }
    bearer(text(data, "access_token")?)?;
    text(data, "refresh_token")?;
    expiry(data)?;
    identity(kind, data)?;
    Ok(())
}

impl SubscriptionAuth {
    async fn owned_lock(&self) -> Result<File, AuthError> {
        let mut path = self.path.as_os_str().to_os_string();
        path.push(".lock");
        tokio::time::timeout(Duration::from_secs(30), lock_file(Path::new(&path)))
            .await
            .map_err(|_| AuthError::Store)?
    }

    pub(super) async fn owned_headers(&self, now: i64) -> Result<Vec<(String, String)>, AuthError> {
        let mut data = read_store(&self.path)?;
        validate_owned(&data, self.kind)?;
        if expiry(&data)? <= now.saturating_add(60) {
            let _lock = self.owned_lock().await?;
            data = read_store(&self.path)?;
            validate_owned(&data, self.kind)?;
            if expiry(&data)? <= now.saturating_add(60) {
                let client_id = self.kind.device_client_id()?;
                let client = BasicClient::new(ClientId::new(client_id.into()))
                    .set_auth_type(AuthType::RequestBody)
                    .set_token_uri(
                        TokenUrl::new(self.token_url.clone())
                            .map_err(|_| AuthError::Configuration)?,
                    );
                let refresh = RefreshToken::new(text(&data, "refresh_token")?.into());
                let http = TokenHttp {
                    client: self.client.clone(),
                    default_bearer: self.kind == Subscription::Kimi,
                    headers: identity(self.kind, &data)?,
                };
                let token = client
                    .exchange_refresh_token(&refresh)
                    .request_async(&http)
                    .await
                    .map_err(|_| AuthError::Refresh)?;
                update(&mut data, &token, now)?;
                write_store(&self.path, &data)?;
            }
        }
        let mut headers = identity(self.kind, &data)?;
        headers.push(bearer(text(&data, "access_token")?)?);
        Ok(headers)
    }

    /// Acquire a fresh, gateway-owned device login, never importing or rotating a CLI's token.
    /// The callback presents the verification URI and user code in the operator's terminal.
    pub async fn login_device(&self, present: impl FnOnce(&str, &str)) -> Result<(), AuthError> {
        let client_id = self.kind.device_client_id()?;
        if self.path.exists() {
            return Err(AuthError::Store);
        }
        let parent = self
            .path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        if !parent.is_dir() {
            return Err(AuthError::Store);
        }
        let _lock = self.owned_lock().await?;
        if self.path.exists() {
            return Err(AuthError::Store);
        }
        let mut data = serde_json::json!({"schema":OWNER, "subscription":self.kind,
            "device_id": uuid::Uuid::new_v4().to_string()});
        let http = TokenHttp {
            client: self.client.clone(),
            default_bearer: self.kind == Subscription::Kimi,
            headers: identity(self.kind, &data)?,
        };
        let client = BasicClient::new(ClientId::new(client_id.into()))
            .set_auth_type(AuthType::RequestBody)
            .set_token_uri(
                TokenUrl::new(if self.kind == Subscription::Copilot {
                    "https://github.com/login/oauth/access_token".into()
                } else {
                    self.token_url.clone()
                })
                .map_err(|_| AuthError::Configuration)?,
            )
            .set_device_authorization_url(
                DeviceAuthorizationUrl::new(self.device_url.clone())
                    .map_err(|_| AuthError::Configuration)?,
            );
        let mut exchange = client.exchange_device_code();
        if self.kind == Subscription::Qwen {
            exchange = exchange
                .add_scope(Scope::new("model:invoke".into()))
                .add_scope(Scope::new("profile:read".into()));
        }
        if self.kind == Subscription::Copilot {
            exchange = exchange.add_scope(Scope::new("read:user".into()));
        }
        let details: DeviceAuthorizationResponse<EmptyExtraDeviceAuthorizationFields> = exchange
            .request_async(&http)
            .await
            .map_err(|_| AuthError::Refresh)?;
        if details.interval().is_zero() || details.expires_in().is_zero() {
            return Err(AuthError::Refresh);
        }
        // The server may supply a complete URI with a user code, but it cannot redirect the
        // operator to another origin. Device codes and secrets never enter operational logs.
        let uri = details
            .verification_uri_complete()
            .map(|v| v.secret().as_str())
            .unwrap_or(details.verification_uri().as_str());
        let parsed = reqwest::Url::parse(uri).map_err(|_| AuthError::Target)?;
        let expected = match self.kind {
            Subscription::Qwen => "chat.qwen.ai",
            Subscription::Copilot => "github.com",
            _ => "auth.kimi.com",
        };
        if parsed.scheme() != "https"
            || parsed.host_str() != Some(expected)
            || !parsed.username().is_empty()
            || parsed.password().is_some()
            || parsed.port().is_some_and(|p| p != 443)
        {
            return Err(AuthError::Target);
        }
        present(uri, details.user_code().secret());
        let token = client
            .exchange_device_access_token(&details)
            .request_async(&http, tokio::time::sleep, Some(Duration::from_secs(900)))
            .await
            .map_err(|_| AuthError::Refresh)?;
        if self.kind == Subscription::Copilot {
            bearer(token.access_token().secret()).map_err(|_| AuthError::Refresh)?;
            data["oauth_token"] = Value::String(token.access_token().secret().clone());
        } else {
            update(&mut data, &token, now_seconds()?)?;
        }
        // Publish without replacing even a non-cooperating writer's new credential file.
        persist_store(&self.path, &data, false)
    }
}

fn update(
    data: &mut Value,
    token: &oauth2::basic::BasicTokenResponse,
    now: i64,
) -> Result<(), AuthError> {
    if token.token_type() != &oauth2::basic::BasicTokenType::Bearer {
        return Err(AuthError::Refresh);
    }
    bearer(token.access_token().secret()).map_err(|_| AuthError::Refresh)?;
    // Both managed providers require a refresh token in successful responses.
    let refresh = token
        .refresh_token()
        .filter(|t| !t.secret().is_empty())
        .ok_or(AuthError::Refresh)?;
    let lifetime = token.expires_in().ok_or(AuthError::Refresh)?.as_secs();
    let lifetime = i64::try_from(lifetime).map_err(|_| AuthError::Refresh)?;
    if lifetime <= 0 {
        return Err(AuthError::Refresh);
    }
    let expires_at = now.checked_add(lifetime).ok_or(AuthError::Refresh)?;
    data["access_token"] = Value::String(token.access_token().secret().clone());
    data["refresh_token"] = Value::String(refresh.secret().clone());
    data["token_type"] = Value::String("Bearer".into());
    data["expires_at"] = Value::from(expires_at);
    Ok(())
}
