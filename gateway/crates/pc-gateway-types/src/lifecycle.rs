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

//! Shared managed-call facts, separate from Core's HTTP check DTOs.
use serde::{Deserialize, Serialize};

/// Sending facts for the whole call, independent of authorization and local delivery.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelSendState {
    /// No attempt has crossed the transport boundary.
    #[default]
    NoSend,
    /// Every attempted send was confirmed unsent by the transport.
    DefinitelyNotSent,
    /// An attempt was handed off without evidence ruling out sending.
    PossiblySent,
    /// A complete upstream response was obtained, regardless of release or decoding.
    ResponseReceived,
}

/// The first cause that actually stopped unfinished local work. This is not a Core verdict.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, thiserror::Error)]
#[serde(rename_all = "snake_case")]
pub enum LifecycleStopReason {
    #[error("deadline_exceeded")]
    DeadlineExceeded,
    #[error("gateway_shutdown")]
    GatewayShutdown,
    #[error("request_cancelled")]
    RequestCancelled,
}

/// The local execution terminal; none of these establishes client receipt.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutionTerminal {
    ReleaseAuthorized,
    Blocked,
    Withheld,
    SendIndeterminate,
    NotSent,
    GatePaused,
    Degraded,
    AdmissionExpired,
    Stopped(LifecycleStopReason),
    #[default]
    Abandoned,
}
