// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

//! Shared ownership and rotation contract for independent gateway refresh grants.

use crate::{
    http::{bearer, TokenHttp},
    oauth::DeviceClient,
    store::{expiry, lock_owned, read_store, text, write_store, OWNED_SCHEMA},
    AuthError,
};
use oauth2::{RefreshToken, TokenResponse};
use serde_json::Value;
use std::path::Path;

fn load(path: &Path, subscription: &str) -> Result<Value, AuthError> {
    let data = read_store(path)?;
    if data["schema"] != OWNED_SCHEMA
        || data["subscription"] != subscription
        || data["token_type"] != "Bearer"
    {
        return Err(AuthError::Credentials);
    }
    bearer(text(&data, "access_token")?)?;
    text(&data, "refresh_token")?;
    expiry(&data)?;
    Ok(data)
}

pub(super) async fn headers(
    path: &Path,
    subscription: &str,
    client: &DeviceClient,
    now: i64,
    transport: impl Fn(&Value) -> Result<TokenHttp, AuthError>,
) -> Result<Vec<(String, String)>, AuthError> {
    let mut data = load(path, subscription)?;
    let mut http = transport(&data)?;
    if expiry(&data)? <= now.saturating_add(60) {
        let _lock = lock_owned(path).await?;
        data = load(path, subscription)?;
        http = transport(&data)?;
        if expiry(&data)? <= now.saturating_add(60) {
            let refresh = RefreshToken::new(text(&data, "refresh_token")?.into());
            let token = client
                .exchange_refresh_token(&refresh)
                .request_async(&http)
                .await
                .map_err(|_| AuthError::Refresh)?;
            update(&mut data, &token, now)?;
            write_store(path, &data)?;
        }
    }
    let mut headers = http.headers;
    headers.push(bearer(text(&data, "access_token")?)?);
    Ok(headers)
}

pub(super) fn update(
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
