/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

mod api;
pub mod wire;
pub use api::{ApiFailure, ServerApi, validate_text};

// Only native adapters use this transport; it is not an IPC fetch primitive.
use crate::{credentials::Secret, error::SafeError};
use reqwest::{
    Certificate, Client,
    header::{AUTHORIZATION, HeaderValue},
    redirect::Policy,
};
use std::{error::Error, sync::Arc, time::Duration};
use tokio::sync::Semaphore;
use url::{Host, Url};

pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(15);
pub const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
pub const MAX_CONCURRENT_READS: usize = 4;

#[derive(Clone)]
pub struct Endpoint(Url);
impl Endpoint {
    pub fn parse(raw: &str) -> Result<Self, SafeError> {
        // Reject ambiguous input before WHATWG URL normalization can erase it.
        if raw.len() > 2048
            || raw.trim() != raw
            || raw.contains(['\\', '?', '#'])
            || raw.bytes().any(|c| c.is_ascii_control())
        {
            return Err(SafeError::InvalidEndpoint);
        }
        let scheme_end = raw.find("://").ok_or(SafeError::InvalidEndpoint)? + 3;
        let tail = &raw[scheme_end..];
        if tail.split('/').next().unwrap_or("").contains('@') {
            return Err(SafeError::InvalidEndpoint);
        }
        let raw_path = tail.find('/').map(|i| &tail[i..]).unwrap_or("");
        if raw_path.split('/').any(|s| !safe_segment(s)) {
            return Err(SafeError::InvalidEndpoint);
        }
        let mut url = Url::parse(raw).map_err(|_| SafeError::InvalidEndpoint)?;
        if !url.username().is_empty() || url.password().is_some() || url.host().is_none() {
            return Err(SafeError::InvalidEndpoint);
        }
        // HTTP trust is based on the literal host, not WHATWG aliases such as 127.1 or integer IPv4.
        let authority = tail.split('/').next().unwrap_or("");
        let literal_host = if authority.starts_with('[') {
            authority
                .split(']')
                .next()
                .unwrap_or("")
                .trim_start_matches('[')
        } else {
            authority.split(':').next().unwrap_or("")
        };
        let literal_loopback = literal_host.eq_ignore_ascii_case("localhost")
            || literal_host
                .parse::<std::net::IpAddr>()
                .is_ok_and(|ip| ip.is_loopback());
        let loopback = literal_loopback
            && match url.host() {
                Some(Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
                Some(Host::Ipv4(ip)) => ip.is_loopback(),
                Some(Host::Ipv6(ip)) => ip.is_loopback(),
                _ => false,
            };
        match url.scheme() {
            "https" => (),
            "http" if loopback => (),
            "http" => return Err(SafeError::InsecureTransport),
            _ => return Err(SafeError::InvalidEndpoint),
        }
        let path = format!("{}/", url.path().trim_end_matches('/'));
        url.set_path(&path);
        Ok(Self(url))
    }
    pub fn is_loopback(&self) -> bool {
        match self.0.host() {
            Some(Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
            Some(Host::Ipv4(ip)) => ip.is_loopback(),
            Some(Host::Ipv6(ip)) => ip.is_loopback(),
            _ => false,
        }
    }
    pub fn as_str(&self) -> &str {
        self.0.as_str()
    }
    fn operation_url(&self, operation: &str) -> Result<Url, SafeError> {
        let manifest: serde_json::Value = serde_json::from_str(include_str!("operations.json"))
            .map_err(|_| SafeError::InvalidResponse)?;
        let path = manifest["operations"][operation]["path"]
            .as_str()
            .ok_or(SafeError::InvalidResponse)?;
        self.0
            .join(path.trim_start_matches('/'))
            .map_err(|_| SafeError::InvalidEndpoint)
    }
}

#[derive(Clone)]
pub struct Transport {
    client: Client,
    slots: Arc<Semaphore>,
}
impl Transport {
    pub fn new(ca_pem: Option<&[u8]>) -> Result<Self, SafeError> {
        let mut builder = Client::builder()
            .no_proxy()
            .retry(reqwest::retry::never())
            .redirect(Policy::none())
            .connect_timeout(CONNECT_TIMEOUT)
            .timeout(REQUEST_TIMEOUT)
            .referer(false);
        if let Some(pem) = ca_pem {
            if pem.len() > 64 * 1024 || String::from_utf8_lossy(pem).contains("PRIVATE KEY") {
                return Err(SafeError::InvalidCertificate);
            }
            let cert = Certificate::from_pem(pem).map_err(|_| SafeError::InvalidCertificate)?;
            builder = builder.add_root_certificate(cert);
        }
        Ok(Self {
            client: builder.build().map_err(|_| SafeError::Tls)?,
            slots: Arc::new(Semaphore::new(MAX_CONCURRENT_READS)),
        })
    }
    /// S1 feasibility probe. Does not infer compatibility, authorization or readiness.
    pub async fn liveness(
        &self,
        endpoint: &Endpoint,
        credential: Option<&Secret>,
    ) -> Result<(), SafeError> {
        let _permit = self.slots.try_acquire().map_err(|_| SafeError::Busy)?;
        let mut request = self.client.get(endpoint.operation_url("get_liveness")?);
        if let Some(secret) = credential {
            let mut header = HeaderValue::from_str(&format!("Bearer {}", secret.expose()))
                .map_err(|_| SafeError::InvalidCredential)?;
            header.set_sensitive(true);
            request = request.header(AUTHORIZATION, header);
        }
        let mut response = request.send().await.map_err(safe_network_error)?;
        match response.status().as_u16() {
            200 => (),
            300..=399 => return Err(SafeError::Redirect),
            401 => return Err(SafeError::Unauthorized),
            403 => return Err(SafeError::Forbidden),
            500..=599 => return Err(SafeError::Server),
            _ => return Err(SafeError::InvalidResponse),
        }
        if response
            .content_length()
            .is_some_and(|n| n > MAX_RESPONSE_BYTES as u64)
        {
            return Err(SafeError::ResponseTooLarge);
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(safe_network_error)? {
            if body.len() + chunk.len() > MAX_RESPONSE_BYTES {
                return Err(SafeError::ResponseTooLarge);
            }
            body.extend_from_slice(&chunk);
        }
        let value: serde_json::Value =
            serde_json::from_slice(&body).map_err(|_| SafeError::InvalidResponse)?;
        if value.get("status").and_then(|s| s.as_str()) != Some("ok") {
            return Err(SafeError::InvalidResponse);
        }
        Ok(())
    }
}
fn safe_network_error(error: reqwest::Error) -> SafeError {
    if error.is_timeout() {
        return SafeError::Timeout;
    }
    // native-tls preserves its typed error in the source chain; never serialize its text.
    let mut source = error.source();
    while let Some(cause) = source {
        if cause.is::<native_tls::Error>() {
            return SafeError::Tls;
        }
        source = cause.source();
    }
    SafeError::Network
}

fn safe_segment(segment: &str) -> bool {
    let bytes = segment.as_bytes();
    let mut decoded = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' {
            if i + 2 >= bytes.len() {
                return false;
            }
            let Some(hi) = (bytes[i + 1] as char).to_digit(16) else {
                return false;
            };
            let Some(lo) = (bytes[i + 2] as char).to_digit(16) else {
                return false;
            };
            decoded.push((hi * 16 + lo) as u8);
            i += 3;
        } else {
            decoded.push(bytes[i]);
            i += 1;
        }
    }
    std::str::from_utf8(&decoded).is_ok()
        && decoded != b"."
        && decoded != b".."
        && !decoded
            .iter()
            .any(|b| b.is_ascii_control() || matches!(b, b'/' | b'\\' | b'%'))
}
