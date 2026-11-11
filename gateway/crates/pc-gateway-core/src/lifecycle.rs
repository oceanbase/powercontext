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

use crate::ManagedOutcome;
use std::sync::{Arc, Mutex, MutexGuard};
use tokio::sync::watch;
use tokio::time::Instant;

pub use pc_gateway_types::{ExecutionTerminal, ModelSendState};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionState {
    NotStarted,
    Running,
    Finished(ExecutionTerminal),
}

/// A bounded, content-free snapshot. Completion does not release retained resources.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExecutionSnapshot {
    pub model_send: ModelSendState,
    pub execution: ExecutionState,
}

struct Shared {
    deadline: Instant,
    request_id: Option<String>,
    call_id: String,
    started: std::time::Instant,
    snapshot: Mutex<ExecutionSnapshot>,
}

impl Shared {
    fn snapshot(&self) -> MutexGuard<'_, ExecutionSnapshot> {
        self.snapshot
            .lock()
            .unwrap_or_else(|error| error.into_inner())
    }
}

/// One-shot control consumed by `Executor::run`. The deadline is control data,
/// not automatic interruption. Obtain cancellation and observation handles before running.
pub struct RequestControl {
    shared: Arc<Shared>,
    cancellation: RequestCancellation,
}

impl RequestControl {
    pub fn new(deadline: Instant, request_id: Option<String>) -> Self {
        Self {
            shared: Arc::new(Shared {
                deadline,
                request_id,
                call_id: pc_gateway_evidence::new_call_id(),
                started: std::time::Instant::now(),
                snapshot: Mutex::new(ExecutionSnapshot {
                    model_send: ModelSendState::NoSend,
                    execution: ExecutionState::NotStarted,
                }),
            }),
            cancellation: RequestCancellation {
                signal: watch::channel(false).0,
            },
        }
    }

    pub fn observation(&self) -> ExecutionObservation {
        ExecutionObservation {
            shared: self.shared.clone(),
        }
    }

    pub fn cancellation(&self) -> RequestCancellation {
        self.cancellation.clone()
    }

    pub(crate) fn start(self) -> Execution {
        self.shared.snapshot().execution = ExecutionState::Running;
        Execution { control: self }
    }
}

/// Shared cancellation intent. Signaling or dropping a handle does not stop execution.
#[derive(Clone)]
pub struct RequestCancellation {
    signal: watch::Sender<bool>,
}

impl RequestCancellation {
    pub fn cancel(&self) {
        self.signal.send_replace(true);
    }

    pub fn is_cancelled(&self) -> bool {
        *self.signal.borrow()
    }

    pub async fn cancelled(&self) {
        let mut receiver = self.signal.subscribe();
        let _ = receiver.wait_for(|cancelled| *cancelled).await;
    }
}

/// Read-only facts and correlation, usable after execution and during local delivery.
/// The request ID is correlation data, never an authenticated identity.
#[derive(Clone)]
pub struct ExecutionObservation {
    shared: Arc<Shared>,
}

impl ExecutionObservation {
    /// Process-local logical call identity; contains no host-provided values.
    pub fn call_id(&self) -> &str {
        &self.shared.call_id
    }

    /// Monotonic milliseconds since the host created request control.
    pub fn elapsed_ms(&self) -> u64 {
        self.shared.started.elapsed().as_millis() as u64
    }

    pub fn deadline(&self) -> Instant {
        self.shared.deadline
    }

    pub fn request_id(&self) -> Option<&str> {
        self.shared.request_id.as_deref()
    }

    pub fn snapshot(&self) -> ExecutionSnapshot {
        *self.shared.snapshot()
    }
}

pub(crate) struct Execution {
    control: RequestControl,
}

impl Execution {
    pub(crate) fn model_send(&mut self, state: ModelSendState) {
        self.control.shared.snapshot().model_send = state;
    }

    pub(crate) fn finish(&mut self, outcome: &ManagedOutcome) {
        self.control.shared.snapshot().execution = ExecutionState::Finished(outcome.terminal());
    }
}

impl Drop for Execution {
    fn drop(&mut self) {
        let mut snapshot = self.control.shared.snapshot();
        if snapshot.execution == ExecutionState::Running {
            snapshot.execution = ExecutionState::Finished(ExecutionTerminal::Abandoned);
        }
    }
}
