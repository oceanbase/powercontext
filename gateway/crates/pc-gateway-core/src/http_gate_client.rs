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
use serde::Serialize;

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
                .timeout(std::time::Duration::from_secs(10))
                .connect_timeout(std::time::Duration::from_secs(3))
                .build()
                .expect("reqwest client build"),
            token: token.filter(|t| !t.is_empty()),
        }
    }

    async fn post<R>(
        &self,
        path: &str,
        body: &impl Serialize,
        resolve_target: bool,
    ) -> Result<R, GateError>
    where
        R: DeserializeOwned,
    {
        let url = format!("{}{}", self.base_url, path);
        let resp = self
            .client
            .post(&url)
            // RFC：DTO 复用 scope-mode/access 声明头。
            .header("x-powercontext-scope-mode", "current")
            .bearer_auth(self.token.clone().unwrap_or_default())
            .header("content-type", "application/json")
            .header("x-powercontext-resolve-target", resolve_target.to_string())
            .json(body)
            .send()
            .await
            .map_err(|e| GateError::GateUnavailable(e.to_string()))?;
        let status = resp.status();
        if !status.is_success() {
            return Err(GateError::GateRejected(status.as_u16()));
        }
        let bytes = resp
            .bytes()
            .await
            .map_err(|e| GateError::GateUnavailable(e.to_string()))?;
        serde_json::from_slice(&bytes)
            .map_err(|e| GateError::Malformed(format!("decode {path}: {e}")))
    }
}

impl GateClient for HttpGateClient {
    async fn check(&self, request: ContextCheckRequest) -> Result<ContextCheckResult, GateError> {
        if matches!(request, ContextCheckRequest::BeforeSend { .. }) {
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
        self.post(
            "/v1/context/check",
            &request,
            matches!(request, ContextCheckRequest::BeforeSend { .. }),
        )
        .await
    }

    async fn capabilities(&self) -> Result<serde_json::Value, GateError> {
        let url = format!("{}/v1/capabilities", self.base_url);
        let resp = self
            .client
            .get(&url)
            .header("x-powercontext-scope-mode", "current")
            .bearer_auth(self.token.clone().unwrap_or_default())
            .send()
            .await
            .map_err(|e| GateError::GateUnavailable(e.to_string()))?;
        let status = resp.status();
        if !status.is_success() {
            return Err(GateError::GateRejected(status.as_u16()));
        }
        resp.json()
            .await
            .map_err(|e| GateError::Malformed(e.to_string()))
    }

    async fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> Result<PrepareContextResult, GateError> {
        self.post("/v1/context/prepare", &request, false).await
    }

    async fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> Result<serde_json::Value, GateError> {
        self.post("/v1/sources/content", &request, false).await
    }
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
