// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::{
    http::{self, bearer, TokenHttp},
    oauth::{authorize_device, DeviceClient},
    store::{lock_new, new_owned_record, persist_store, read_store, text},
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
    pub(super) oauth: DeviceClient,
}

impl Copilot {
    pub(super) fn new() -> Result<Self, AuthError> {
        Ok(Self {
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

    pub(super) fn headers(&self, path: &Path) -> Result<Vec<(String, String)>, AuthError> {
        // Match the public Pydantic AI provider: current Copilot accepts the GitHub
        // OAuth token directly, including grants managed by an existing gh login.
        let data = read_store(path)?;
        copilot_headers(text(&data, "oauth_token")?)
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
