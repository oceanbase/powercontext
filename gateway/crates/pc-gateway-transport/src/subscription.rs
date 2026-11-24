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

use crate::{AdmittedSend, NotSentReason, ReqwestTransport, SendOutcome, Transport};
use std::ffi::OsString;

/// One HTTP egress or a configured CLI execution boundary; no provider routing table.
pub struct GatewayTransport {
    http: ReqwestTransport,
    claude: Option<OsString>,
}

impl GatewayTransport {
    pub fn new(http: ReqwestTransport) -> Self {
        Self { http, claude: None }
    }
    pub fn with_claude(mut self, executable: Option<OsString>) -> Self {
        self.claude = executable;
        self
    }
}

impl Transport for GatewayTransport {
    async fn send(&self, send: AdmittedSend) -> SendOutcome {
        if send.target_url == "claude-cli://execute" {
            let Some(executable) = self.claude.as_ref() else {
                return SendOutcome::NotSent(NotSentReason::RejectedLocally);
            };
            crate::claude::send(executable, send).await
        } else {
            self.http.send(send).await
        }
    }

    async fn cancel(&self, send: &AdmittedSend) {
        self.http.cancel(send).await;
    }
}
