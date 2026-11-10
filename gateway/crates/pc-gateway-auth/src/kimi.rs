// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

// Provider OAuth contract follows Kimi Code; see LICENSES/oauth.md.
use crate::{
    http::{self, checked_header, TokenHttp},
    managed,
    oauth::{authorize_device, now_seconds, DeviceClient},
    store::{lock_new, new_owned_record, persist_store, text},
    AuthError,
};
use oauth2::{basic::BasicClient, AuthType, ClientId, DeviceAuthorizationUrl, TokenUrl};
use serde_json::Value;
use std::path::Path;

pub(super) const CHAT_TARGET: &str = "https://api.kimi.com/coding/v1/chat/completions";
pub(super) const RESPONSES_TARGET: &str = "https://api.kimi.com/coding/v1/responses";

pub(super) fn validate_target(target: &str) -> Result<(), AuthError> {
    http::validate_target(
        target,
        &[
            CHAT_TARGET,
            RESPONSES_TARGET,
            "https://api.kimi.ai/coding/v1/chat/completions",
            "https://api.kimi.ai/coding/v1/responses",
        ],
    )
}

pub(super) struct Kimi {
    pub(super) oauth: DeviceClient,
}

impl Kimi {
    pub(super) fn new() -> Result<Self, AuthError> {
        Ok(Self {
            oauth: BasicClient::new(ClientId::new("17e5f671-d194-4dfb-9706-5516cb48c098".into()))
                .set_auth_type(AuthType::RequestBody)
                .set_token_uri(
                    TokenUrl::new("https://auth.kimi.com/api/oauth/token".into())
                        .map_err(|_| AuthError::Configuration)?,
                )
                .set_device_authorization_url(
                    DeviceAuthorizationUrl::new(
                        "https://auth.kimi.com/api/oauth/device_authorization".into(),
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
        managed::headers(path, "kimi", &self.oauth, now, |data| {
            Ok(TokenHttp {
                client: client.clone(),
                default_bearer: true,
                headers: identity(data)?,
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
        let mut data = new_owned_record("kimi");
        let http = TokenHttp {
            client: client.clone(),
            default_bearer: true,
            headers: identity(&data)?,
        };
        let token = authorize_device(&self.oauth, &http, "auth.kimi.com", &[], present).await?;
        managed::update(&mut data, &token, now_seconds()?)?;
        persist_store(path, &data, false)
    }
}

fn identity(data: &Value) -> Result<Vec<(String, String)>, AuthError> {
    let mut headers = vec![(
        "user-agent".into(),
        format!("PowerContextGateway/{}", env!("CARGO_PKG_VERSION")),
    )];
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
    Ok(headers)
}
