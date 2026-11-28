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
    CaptureContentSourcesRequest, ContextCheckRequest, ContextCheckResult, PrepareContextRequest,
    PrepareContextResult,
};
use serde::de::DeserializeOwned;
use std::time::Duration;
use url::Host;

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
                // A proxy could impersonate a loopback Core and forge privileged target approval.
                // Keep every Core RPC on a direct connection, regardless of ambient proxy settings.
                .no_proxy()
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
                && match url.host() {
                    Some(Host::Ipv4(host)) => host.is_loopback(),
                    Some(Host::Ipv6(host)) => host.is_loopback(),
                    _ => false,
                };
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
        request: CaptureContentSourcesRequest,
    ) -> Result<serde_json::Value, GateError> {
        self.request(
            reqwest::Method::POST,
            "/v1/sources/content/batch",
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

    fn before_send() -> ContextCheckRequest {
        ContextCheckRequest::BeforeSend {
            scope_id: "test".into(),
            target_profile: "test".into(),
            payload: ModelJsonPayload::new(
                BTreeMap::from([("content-type".into(), "application/json".into())]),
                "{}".into(),
            ),
            prepared_contexts: vec![],
        }
    }

    #[tokio::test]
    async fn plain_http_requires_a_literal_loopback_address() {
        for base in [
            "http://model.example",
            "http://localhost:8000",
            "http://192.0.2.1:8000",
            "http://[2001:db8::1]:8000",
        ] {
            let client = HttpGateClient::with_token(base, Some("private-token".into()));
            let verdict = client.check(before_send()).await;
            assert!(matches!(verdict, Err(GateError::Malformed(_))), "{base}");
        }
    }

    #[tokio::test]
    async fn ipv4_and_ipv6_loopback_can_resolve_targets_over_http() {
        use axum::{http::HeaderMap, routing::post, Json, Router};

        for address in ["127.0.0.1:0", "[::1]:0"] {
            let listener = tokio::net::TcpListener::bind(address)
                .await
                .expect("loopback listener must bind");
            let base = format!("http://{}", listener.local_addr().unwrap());
            let app = Router::new().route(
                "/v1/context/check",
                post(
                    |headers: HeaderMap, Json(request): Json<ContextCheckRequest>| async move {
                        assert_eq!(headers["authorization"], "Bearer private-token");
                        assert_eq!(headers["x-powercontext-resolve-target"], "true");
                        assert!(matches!(request, ContextCheckRequest::BeforeSend { .. }));
                        Json(serde_json::json!({
                            "schema": "powercontext.context-check.v1",
                            "phase": "before_send",
                            "decision": "allow",
                            "reason_codes": [],
                            "coverage": {"profile": "test", "complete": true},
                            "policy_version": "test",
                            "binding": "loopback-binding",
                            "valid_until": "2999-01-01T00:00:00Z",
                            "target_endpoint": "https://model.example/v1/chat/completions",
                            "target_protocol": "openai-chat"
                        }))
                    },
                ),
            );
            let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
            let client = HttpGateClient::with_token(&base, Some("private-token".into()));
            let result = client.check(before_send()).await;
            server.abort();
            let result = result.unwrap_or_else(|error| panic!("{base}: {error:?}"));
            assert_eq!(result.binding.as_deref(), Some("loopback-binding"));
            assert_eq!(
                result.target_endpoint.as_deref(),
                Some("https://model.example/v1/chat/completions")
            );
            assert_eq!(result.target_protocol.as_deref(), Some("openai-chat"));
        }
    }
}
