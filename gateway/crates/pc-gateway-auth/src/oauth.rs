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

use crate::{http::TokenHttp, AuthError};
use oauth2::{
    basic::{BasicClient, BasicTokenResponse},
    DeviceAuthorizationResponse, EmptyExtraDeviceAuthorizationFields, EndpointNotSet, EndpointSet,
    Scope,
};
use std::time::Duration;

pub(super) type DeviceClient =
    BasicClient<EndpointNotSet, EndpointSet, EndpointNotSet, EndpointNotSet, EndpointSet>;

pub(super) async fn authorize_device(
    client: &DeviceClient,
    http: &TokenHttp,
    verification_host: &str,
    scopes: &[&str],
    present: impl FnOnce(&str, &str),
) -> Result<BasicTokenResponse, AuthError> {
    let mut exchange = client.exchange_device_code();
    for scope in scopes {
        exchange = exchange.add_scope(Scope::new((*scope).into()));
    }
    let details: DeviceAuthorizationResponse<EmptyExtraDeviceAuthorizationFields> = exchange
        .request_async(http)
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
    if parsed.scheme() != "https"
        || parsed.host_str() != Some(verification_host)
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.port().is_some_and(|p| p != 443)
    {
        return Err(AuthError::Target);
    }
    present(uri, details.user_code().secret());
    let token = client
        .exchange_device_access_token(&details)
        .request_async(http, tokio::time::sleep, Some(Duration::from_secs(900)))
        .await
        .map_err(|_| AuthError::Refresh)?;

    Ok(token)
}

pub(super) fn now_seconds() -> Result<i64, AuthError> {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|value| value.as_secs() as i64)
        .map_err(|_| AuthError::Configuration)
}
