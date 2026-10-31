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

//! Core 连接形态：UDS（同机组合部署）或 TCP（独立运行）。

use pc_gateway_core::{GateClient, GateError, HttpGateClient, UdsGateClient};
use pc_gateway_types::{
    CaptureContentSourceRequest, ContextCheckRequest, ContextCheckResult, PrepareContextRequest,
    PrepareContextResult,
};

pub enum CoreLink {
    Uds(UdsGateClient),
    Http(HttpGateClient),
}

impl CoreLink {
    pub fn from_env() -> Self {
        if let Ok(sock) = std::env::var("PC_GATEWAY_CORE_UDS") {
            tracing::info!(%sock, "core link: UDS (combined deployment)");
            Self::Uds(UdsGateClient::new(sock))
        } else {
            let base = std::env::var("PC_GATEWAY_CORE_BASE")
                .unwrap_or_else(|_| "http://127.0.0.1:8000".into());
            tracing::info!(%base, "core link: TCP (standalone deployment)");
            Self::Http(HttpGateClient::new(base))
        }
    }
}

impl GateClient for CoreLink {
    async fn check(&self, request: ContextCheckRequest) -> Result<ContextCheckResult, GateError> {
        match self {
            CoreLink::Uds(c) => c.check(request).await,
            CoreLink::Http(c) => c.check(request).await,
        }
    }

    async fn capabilities(&self) -> Result<serde_json::Value, GateError> {
        match self {
            CoreLink::Uds(c) => c.capabilities().await,
            CoreLink::Http(c) => c.capabilities().await,
        }
    }

    async fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> Result<PrepareContextResult, GateError> {
        match self {
            CoreLink::Uds(c) => c.prepare(request).await,
            CoreLink::Http(c) => c.prepare(request).await,
        }
    }

    async fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> Result<serde_json::Value, GateError> {
        match self {
            CoreLink::Uds(c) => c.capture(request).await,
            CoreLink::Http(c) => c.capture(request).await,
        }
    }
}
