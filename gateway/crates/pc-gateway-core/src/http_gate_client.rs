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
            client: reqwest::Client::new(),
            token: token.filter(|t| !t.is_empty()),
        }
    }

    async fn post<R>(&self, path: &str, body: &impl Serialize) -> Result<R, GateError>
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
            .json(body)
            .send()
            .await
            .map_err(|e| GateError::GateUnavailable(e.to_string()))?;
        let status = resp.status();
        if !status.is_success() {
            return Err(GateError::GateUnavailable(format!(
                "gate returned HTTP {status}"
            )));
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
    async fn check(
        &self,
        request: ContextCheckRequest,
    ) -> Result<ContextCheckResult, GateError> {
        self.post("/v1/context/check", &request).await
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
            return Err(GateError::GateUnavailable(format!(
                "capabilities HTTP {status}"
            )));
        }
        resp.json()
            .await
            .map_err(|e| GateError::Malformed(e.to_string()))
    }

    async fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> Result<PrepareContextResult, GateError> {
        self.post("/v1/context/prepare", &request).await
    }

    async fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> Result<serde_json::Value, GateError> {
        self.post("/v1/sources/content", &request).await
    }
}
