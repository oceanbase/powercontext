// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

// CLI format and principal-aware refresh follow republic; see LICENSES/oauth.md.
use crate::{
    http::{self, bearer, TokenHttp},
    store::{expiry, lock_file, read_store, text, write_store},
    AuthError,
};
use oauth2::{basic::BasicClient, AuthType, ClientId, RefreshToken, TokenResponse, TokenUrl};
use serde_json::Value;
use std::{path::Path, time::Duration};

pub const GROK_ISSUER: &str = "https://auth.x.ai";
pub const GROK_CLIENT_ID: &str = "b1a00492-073a-47ea-816f-4c329264a828";
pub const GROK_SCOPE: &str = "https://auth.x.ai::b1a00492-073a-47ea-816f-4c329264a828";
pub(super) const CHAT_TARGET: &str = "https://api.x.ai/v1/chat/completions";
pub(super) const RESPONSES_TARGET: &str = "https://api.x.ai/v1/responses";

pub(super) fn validate_target(target: &str) -> Result<(), AuthError> {
    http::validate_target(target, &[CHAT_TARGET, RESPONSES_TARGET])
}

pub(super) struct Grok {
    pub(super) token_url: String,
}

impl Grok {
    pub(super) fn new() -> Self {
        Self {
            token_url: "https://auth.x.ai/oauth2/token".into(),
        }
    }
    pub(super) async fn headers(
        &self,
        path: &Path,
        http_client: &reqwest::Client,
        now: i64,
    ) -> Result<Vec<(String, String)>, AuthError> {
        let data = read_store(path)?;
        let stored = grok_token(&data)?;
        if expiry(stored)? > now.saturating_add(60) {
            return Ok(vec![bearer(text(stored, "key")?)?]);
        }
        let _lock = tokio::time::timeout(
            Duration::from_secs(30),
            lock_file(&path.with_file_name("auth.json.lock")),
        )
        .await
        .map_err(|_| AuthError::Store)??;
        // Another process or instance may have rotated the refresh token while we waited.
        let mut data = read_store(path)?;
        let stored = grok_token(&data)?;
        if expiry(stored)? > now.saturating_add(60) {
            return Ok(vec![bearer(text(stored, "key")?)?]);
        }
        let refresh = text(stored, "refresh_token")?;
        let client = BasicClient::new(ClientId::new(GROK_CLIENT_ID.into()))
            .set_auth_type(AuthType::RequestBody)
            .set_token_uri(TokenUrl::new(self.token_url.clone()).map_err(|_| AuthError::Refresh)?);
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
            client: http_client.clone(),
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
        let date = chrono::DateTime::from_timestamp(expires_at, 0).ok_or(AuthError::Refresh)?;
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
        write_store(path, &data)?;
        Ok(vec![bearer(access)?])
    }
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
