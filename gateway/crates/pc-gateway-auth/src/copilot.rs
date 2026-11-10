// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::{
    http::{self, bearer, checked_header, TokenHttp},
    oauth::{authorize_device, DeviceClient},
    store::{lock_new, new_owned_record, persist_store, read_store, text, STORE_LIMIT},
    AuthError,
};
use oauth2::{
    basic::BasicClient, AuthType, ClientId, DeviceAuthorizationUrl, TokenResponse, TokenUrl,
};
use serde_json::Value;
use std::path::Path;

pub(super) const CHAT_TARGET: &str = "https://api.githubcopilot.com/chat/completions";
pub(super) const RESPONSES_TARGET: &str = "https://api.githubcopilot.com/responses";

pub(super) fn validate_target(target: &str) -> Result<(), AuthError> {
    http::validate_target(target, &[CHAT_TARGET, RESPONSES_TARGET])
}

pub(super) struct Copilot {
    pub(super) session_url: String,
    pub(super) oauth: DeviceClient,
    session: tokio::sync::Mutex<Option<Session>>,
}

struct Session {
    github_token: String,
    token: String,
    expires_at: i64,
}

impl Copilot {
    pub(super) fn new() -> Result<Self, AuthError> {
        Ok(Self {
            session_url: "https://api.github.com/copilot_internal/v2/token".into(),
            oauth: BasicClient::new(ClientId::new("Iv1.b507a08c87ecfe98".into()))
                .set_auth_type(AuthType::RequestBody)
                .set_token_uri(
                    TokenUrl::new("https://github.com/login/oauth/access_token".into())
                        .map_err(|_| AuthError::Configuration)?,
                )
                .set_device_authorization_url(
                    DeviceAuthorizationUrl::new("https://github.com/login/device/code".into())
                        .map_err(|_| AuthError::Configuration)?,
                ),
            session: tokio::sync::Mutex::new(None),
        })
    }

    pub(super) async fn login(
        &self,
        path: &Path,
        client: &reqwest::Client,
        present: impl FnOnce(&str, &str),
    ) -> Result<(), AuthError> {
        let _lock = lock_new(path).await?;
        let mut data = new_owned_record("copilot");
        let http = TokenHttp {
            client: client.clone(),
            default_bearer: false,
            headers: vec![(
                "user-agent".into(),
                concat!("PowerContextGateway/", env!("CARGO_PKG_VERSION")).into(),
            )],
        };
        let token =
            authorize_device(&self.oauth, &http, "github.com", &["read:user"], present).await?;
        bearer(token.access_token().secret()).map_err(|_| AuthError::Refresh)?;
        data["oauth_token"] = Value::String(token.access_token().secret().clone());
        persist_store(path, &data, false)
    }

    pub(super) async fn headers(
        &self,
        path: &Path,
        client: &reqwest::Client,
        now: i64,
    ) -> Result<Vec<(String, String)>, AuthError> {
        let data = read_store(path)?;
        let github = text(&data, "oauth_token")?;
        let mut session = self.session.lock().await;
        if let Some(current) = session
            .as_ref()
            .filter(|s| s.github_token == github && s.expires_at > now.saturating_add(120))
        {
            return copilot_headers(&current.token);
        }
        // This GitHub-specific session exchange is not an RFC 6749 token grant.
        let mut request = client
            .get(&self.session_url)
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
        *session = Some(Session {
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
