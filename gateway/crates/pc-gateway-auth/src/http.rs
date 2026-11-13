// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::{store::STORE_LIMIT, AuthError};
use serde_json::Value;
use std::time::Duration;

pub(super) fn client() -> Result<reqwest::Client, AuthError> {
    reqwest::Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .retry(reqwest::retry::never())
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|_| AuthError::Configuration)
}

pub(super) fn validate_target(target: &str, allowed: &[&str]) -> Result<(), AuthError> {
    let url = reqwest::Url::parse(target).map_err(|_| AuthError::Target)?;
    if url.scheme() != "https"
        || !url.username().is_empty()
        || url.password().is_some()
        || url.port().is_some_and(|port| port != 443)
        || url.query().is_some()
        || url.fragment().is_some()
        || !allowed.contains(&url.as_str())
    {
        return Err(AuthError::Target);
    }
    Ok(())
}

pub(super) fn checked_header(name: &str, value: &str) -> Result<(String, String), AuthError> {
    if value.is_empty() || reqwest::header::HeaderValue::from_str(value).is_err() {
        return Err(AuthError::Credentials);
    }
    Ok((name.into(), value.into()))
}

pub(super) fn bearer(token: &str) -> Result<(String, String), AuthError> {
    if token.is_empty() || !token.bytes().all(|byte| byte.is_ascii_graphic()) {
        return Err(AuthError::Credentials);
    }
    checked_header("authorization", &format!("Bearer {token}"))
}

pub(super) struct TokenHttp {
    pub(super) client: reqwest::Client,
    pub(super) default_bearer: bool,
    pub(super) headers: Vec<(String, String)>,
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
        // Some grant endpoints omit token_type. The provider must opt into a Bearer default;
        // OAuth parsing and grant validation remain owned by oauth2.
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
