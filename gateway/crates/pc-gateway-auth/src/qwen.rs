// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

// Provider OAuth contract follows Qwen Code; see LICENSES/oauth.md.
use crate::{
    http::{self, TokenHttp},
    managed,
    oauth::{authorize_device, now_seconds, DeviceClient},
    store::{lock_new, new_owned_record, persist_store},
    AuthError,
};
use oauth2::{basic::BasicClient, AuthType, ClientId, DeviceAuthorizationUrl, TokenUrl};
use std::path::Path;

pub(super) const CHAT_TARGET: &str = "https://api.qwen.ai/compatible-mode/v1/chat/completions";
pub(super) const RESPONSES_TARGET: &str = "";

pub(super) fn validate_target(target: &str) -> Result<(), AuthError> {
    http::validate_target(target, &[CHAT_TARGET])
}

pub(super) struct Qwen {
    pub(super) oauth: DeviceClient,
}

impl Qwen {
    pub(super) fn new() -> Result<Self, AuthError> {
        Ok(Self {
            oauth: BasicClient::new(ClientId::new("qwen-code-cli".into()))
                .set_auth_type(AuthType::RequestBody)
                .set_token_uri(
                    TokenUrl::new("https://auth.qwen.ai/api/oauth/token".into())
                        .map_err(|_| AuthError::Configuration)?,
                )
                .set_device_authorization_url(
                    DeviceAuthorizationUrl::new(
                        "https://auth.qwen.ai/api/oauth/device/code".into(),
                    )
                    .map_err(|_| AuthError::Configuration)?,
                ),
        })
    }

    pub(super) async fn headers(
        &self,
        path: &Path,
        client: &reqwest::Client,
        now: i64,
    ) -> Result<Vec<(String, String)>, AuthError> {
        managed::headers(path, "qwen", &self.oauth, now, |_| {
            Ok(TokenHttp {
                client: client.clone(),
                default_bearer: false,
                headers: identity(),
            })
        })
        .await
    }

    pub(super) async fn login(
        &self,
        path: &Path,
        client: &reqwest::Client,
        present: impl FnOnce(&str, &str),
    ) -> Result<(), AuthError> {
        let _lock = lock_new(path).await?;
        let mut data = new_owned_record("qwen");
        let http = TokenHttp {
            client: client.clone(),
            default_bearer: false,
            headers: identity(),
        };
        let token = authorize_device(
            &self.oauth,
            &http,
            "chat.qwen.ai",
            &["model:invoke", "profile:read"],
            present,
        )
        .await?;
        managed::update(&mut data, &token, now_seconds()?)?;
        persist_store(path, &data, false)
    }
}

fn identity() -> Vec<(String, String)> {
    vec![(
        "user-agent".into(),
        concat!("PowerContextGateway/", env!("CARGO_PKG_VERSION")).into(),
    )]
}
