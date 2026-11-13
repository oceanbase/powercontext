// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

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
