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

//! Deployment-owned model ingress credentials and listener policy.

use axum::http::{header, HeaderMap};
use std::env::VarError;
use std::net::SocketAddr;
use subtle::ConstantTimeEq;

pub fn load() -> Result<(SocketAddr, Option<String>), &'static str> {
    let listen = match std::env::var("PC_GATEWAY_LISTEN") {
        Ok(value) => value,
        Err(VarError::NotPresent) => "127.0.0.1:8787".into(),
        Err(VarError::NotUnicode(_)) => return Err("PC_GATEWAY_LISTEN must be an IP address:port"),
    };
    let addr: SocketAddr = listen
        .parse()
        .map_err(|_| "PC_GATEWAY_LISTEN must be an IP address:port")?;
    let key = match std::env::var("PC_GATEWAY_API_KEY") {
        Ok(value) if !value.is_empty() => Some(value),
        Ok(_) | Err(VarError::NotPresent) => None,
        Err(VarError::NotUnicode(_)) => {
            return Err("PC_GATEWAY_API_KEY must be a valid Bearer token")
        }
    };
    if key.as_deref().is_some_and(|value| !is_bearer_token(value)) {
        return Err("PC_GATEWAY_API_KEY must be a valid Bearer token");
    }
    if !addr.ip().is_loopback() && key.is_none() {
        return Err("PC_GATEWAY_API_KEY is required for non-loopback listeners");
    }
    Ok((addr, key))
}

pub fn authenticate(expected: &str, headers: &HeaderMap) -> bool {
    let mut values = headers.get_all(header::AUTHORIZATION).iter();
    let Some(value) = values.next() else {
        return false;
    };
    if values.next().is_some() {
        return false;
    }
    let Ok(value) = value.to_str() else {
        return false;
    };
    let Some((scheme, credential)) = value.trim_matches([' ', '\t']).split_once(' ') else {
        return false;
    };
    let credential = credential.trim_start_matches(' ');
    scheme.eq_ignore_ascii_case("Bearer")
        && is_bearer_token(credential)
        && bool::from(expected.as_bytes().ct_eq(credential.as_bytes()))
}

fn is_bearer_token(value: &str) -> bool {
    // RFC 6750: at least one token character followed by optional '=' padding.
    let token = value.trim_end_matches('=');
    !token.is_empty()
        && token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-._~+/".contains(&byte))
}
