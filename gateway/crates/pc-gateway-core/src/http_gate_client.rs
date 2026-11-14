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

use crate::{GateClient, GateError};
use pc_gateway_types::{
    CaptureContentSourceRequest, ContextCheckRequest, ContextCheckResult, PrepareContextRequest,
    PrepareContextResult,
};
use serde::de::DeserializeOwned;
use std::time::Duration;

/// 独立部署形态：HTTPS/TCP 直连 PowerContext Server 的公开契约。
/// 同机组合部署用 `UdsGateClient`（HTTP over Unix Domain Socket）。
#[derive(Debug, Clone)]
pub struct HttpGateClient {
    base_url: String,
    client: reqwest::Client,
    /// Server 认证令牌（POWERCONTEXT_SERVER_AUTH_TOKEN）；None 时不带认证头。
    token: Option<String>,
}

impl HttpGateClient {
    pub fn new(base_url: impl Into<String>) -> Self {
        Self::with_token(base_url, std::env::var("PC_GATEWAY_CORE_TOKEN").ok())
    }

    pub fn with_token(base_url: impl Into<String>, token: Option<String>) -> Self {
        Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            client: reqwest::Client::builder()
                .redirect(reqwest::redirect::Policy::none())
                .retry(reqwest::retry::never())
                // Avoid hyper's implicit stale pooled-connection resend as well.
                .http1_only()
                .pool_max_idle_per_host(0)
                .timeout(std::time::Duration::from_secs(10))
                .connect_timeout(std::time::Duration::from_secs(3))
                .build()
                .expect("reqwest client build"),
            token: token.filter(|t| !t.is_empty()),
        }
    }

    /// Serialize once before entering recovery. The operation budget includes both
    /// attempts and full response consumption; the managed-call deadline may stop it earlier.
    async fn request<R: DeserializeOwned>(
        &self,
        method: reqwest::Method,
        path: &str,
        body: Option<Vec<u8>>,
        replay_safe: bool,
        resolve_target: bool,
    ) -> Result<R, GateError> {
        let url = format!("{}{}", self.base_url, path);
        tokio::time::timeout(Duration::from_secs(10), async {
            for attempt in 0..2 {
                if attempt != 0 {
                    // Return to the enclosing lifecycle before starting recovery, even
                    // when the failed socket completed synchronously in this poll.
                    tokio::task::yield_now().await;
                }
                let mut request = self
                    .client
                    .request(method.clone(), &url)
                    .header("x-powercontext-scope-mode", "current")
                    .bearer_auth(self.token.clone().unwrap_or_default());
                if resolve_target {
                    request = request.header("x-powercontext-resolve-target", "true");
                }
                if let Some(body) = &body {
                    request = request
                        .header("content-type", "application/json")
                        .body(body.clone());
                }
                let response = match request.send().await {
                    Ok(response) => response,
                    Err(error) => {
                        if malformed_http(&error) {
                            return Err(GateError::Malformed("invalid http response".into()));
                        }
                        // Connect failure is the only reqwest transport proof of zero
                        // application-request handoff. EOF and timeout provide no such proof.
                        if attempt == 0
                            && !error.is_builder()
                            && (replay_safe || error.is_connect())
                        {
                            continue;
                        }
                        return Err(GateError::GateUnavailable("http request failed".into()));
                    }
                };
                if !response.status().is_success() {
                    return Err(GateError::GateRejected(response.status().as_u16()));
                }
                let bytes = match response.bytes().await {
                    Ok(bytes) => bytes,
                    Err(error) if malformed_http(&error) => {
                        return Err(GateError::Malformed("invalid http response body".into()));
                    }
                    Err(_) if attempt == 0 && replay_safe => continue,
                    Err(_) => {
                        return Err(GateError::GateUnavailable(
                            "http response interrupted".into(),
                        ))
                    }
                };
                return serde_json::from_slice(&bytes)
                    .map_err(|_| GateError::Malformed(format!("decode {path}")));
            }
            unreachable!("the last attempt always returns")
        })
        .await
        .map_err(|_| GateError::GateUnavailable("http operation timeout".into()))?
    }
}

impl GateClient for HttpGateClient {
    async fn check(&self, request: ContextCheckRequest) -> Result<ContextCheckResult, GateError> {
        let resolve_target = matches!(request, ContextCheckRequest::BeforeSend { .. });
        if resolve_target {
            let url = reqwest::Url::parse(&self.base_url)
                .map_err(|_| GateError::Malformed("invalid Core URL".into()))?;
            let local_http = url.scheme() == "http"
                && url
                    .host_str()
                    .and_then(|host| host.parse::<std::net::IpAddr>().ok())
                    .is_some_and(|host| host.is_loopback());
            if url.scheme() != "https" && !local_http {
                return Err(GateError::Malformed(
                    "privileged target resolution requires HTTPS or loopback Core".into(),
                ));
            }
        }
        self.request(
            reqwest::Method::POST,
            "/v1/context/check",
            Some(serde_json::to_vec(&request).expect("serialize check")),
            true,
            resolve_target,
        )
        .await
    }

    async fn capabilities(&self) -> Result<serde_json::Value, GateError> {
        self.request(reqwest::Method::GET, "/v1/capabilities", None, true, false)
            .await
    }

    async fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> Result<PrepareContextResult, GateError> {
        self.request(
            reqwest::Method::POST,
            "/v1/context/prepare",
            Some(serde_json::to_vec(&request).expect("serialize prepare")),
            false,
            false,
        )
        .await
    }

    async fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> Result<serde_json::Value, GateError> {
        self.request(
            reqwest::Method::POST,
            "/v1/sources/content",
            Some(serde_json::to_vec(&request).expect("serialize capture")),
            true,
            false,
        )
        .await
    }
}

// Use typed protocol evidence, never an error-string heuristic for replay decisions.
fn malformed_http(error: &reqwest::Error) -> bool {
    use std::error::Error;
    let mut cause: Option<&(dyn Error + 'static)> = Some(error);
    while let Some(error) = cause {
        if error
            .downcast_ref::<hyper::Error>()
            .is_some_and(hyper::Error::is_parse)
            || error.downcast_ref::<std::io::Error>().is_some_and(|e| {
                // Hyper's HTTP/1 chunk decoder uses both kinds for invalid framing.
                matches!(
                    e.kind(),
                    std::io::ErrorKind::InvalidData | std::io::ErrorKind::InvalidInput
                )
            })
        {
            return true;
        }
        cause = error.source();
    }
    false
}

#[cfg(test)]
mod target_security_tests {
    use super::*;
    use pc_gateway_types::ModelJsonPayload;
    use std::collections::BTreeMap;

    #[tokio::test]
    async fn remote_plain_http_cannot_receive_privileged_target_resolution_token() {
        let client =
            HttpGateClient::with_token("http://model.example", Some("private-token".into()));
        let verdict = client
            .check(ContextCheckRequest::BeforeSend {
                scope_id: "test".into(),
                target_profile: "test".into(),
                payload: ModelJsonPayload::new(
                    BTreeMap::from([("content-type".into(), "application/json".into())]),
                    "{}".into(),
                ),
                prepared_contexts: vec![],
            })
            .await;
        assert!(matches!(verdict, Err(GateError::Malformed(_))));
    }
}
