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
use std::future::Future;
use std::sync::{Arc, Mutex, MutexGuard};
use tokio::sync::watch;
use tokio::time::Instant;

pub use pc_gateway_types::{ExecutionTerminal, LifecycleStopReason, ModelSendState};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionState {
    NotStarted,
    Running,
    Finished(ExecutionTerminal),
}

/// Observations made by the local delivery owner, never a client receipt.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocalDeliveryState {
    NotStarted,
    InProgress,
    Completed,
    Interrupted(LifecycleStopReason),
}

/// A bounded, content-free snapshot. Completion does not release retained resources.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExecutionSnapshot {
    pub model_send: ModelSendState,
    pub execution: ExecutionState,
    pub stop_reason: Option<LifecycleStopReason>,
    pub local_delivery: LocalDeliveryState,
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

/// One-shot execution control. Obtain the lifecycle and observation before running.
pub struct RequestControl {
    lifecycle: RequestLifecycle,
}

impl RequestControl {
    pub fn new(deadline: Instant, request_id: Option<String>) -> Self {
        Self {
            lifecycle: RequestLifecycle {
                shared: Arc::new(Shared {
                    deadline,
                    request_id,
                    call_id: pc_gateway_evidence::new_call_id(),
                    started: std::time::Instant::now(),
                    snapshot: Mutex::new(ExecutionSnapshot {
                        model_send: ModelSendState::NoSend,
                        execution: ExecutionState::NotStarted,
                        stop_reason: None,
                        local_delivery: LocalDeliveryState::NotStarted,
                    }),
                }),
                cancellation: RequestCancellation {
                    signal: watch::channel(None).0,
                },
            },
        }
    }

    /// Retain across body intake, execution, and response handoff. Never resets the deadline.
    pub fn lifecycle(&self) -> RequestLifecycle {
        self.lifecycle.clone()
    }

    pub fn observation(&self) -> ExecutionObservation {
        self.lifecycle.observation()
    }

    pub fn cancellation(&self) -> RequestCancellation {
        self.lifecycle.cancellation()
    }

    pub(crate) fn start(self) -> Execution {
        self.lifecycle.shared.snapshot().execution = ExecutionState::Running;
        Execution { control: self }
    }
}

/// Cloneable control for the actual owner of each stage. Cancellation drops inline work;
/// callers that spawn tasks must also join those tasks before claiming reclamation.
#[derive(Clone)]
pub struct RequestLifecycle {
    shared: Arc<Shared>,
    cancellation: RequestCancellation,
}

impl RequestLifecycle {
    /// The response is handed to its actual delivery owner. Body EOF is not completion.
    pub fn begin_local_delivery(&self) {
        let mut snapshot = self.shared.snapshot();
        if snapshot.local_delivery == LocalDeliveryState::NotStarted {
            snapshot.local_delivery = LocalDeliveryState::InProgress;
        }
    }

    /// Call only after normal completion of the model's closing HTTP/1 connection.
    /// This does not claim the client application read or accepted the response.
    pub fn complete_local_delivery(&self) {
        let mut snapshot = self.shared.snapshot();
        if snapshot.local_delivery == LocalDeliveryState::InProgress {
            snapshot.local_delivery = LocalDeliveryState::Completed;
        }
    }

    /// The delivery owner stopped unfinished output. Execution/send facts are preserved.
    pub fn interrupt_local_delivery(&self, reason: LifecycleStopReason) {
        let mut snapshot = self.shared.snapshot();
        if snapshot.local_delivery == LocalDeliveryState::InProgress {
            snapshot.stop_reason = Some(snapshot.stop_reason.unwrap_or(reason));
            snapshot.local_delivery =
                LocalDeliveryState::Interrupted(snapshot.stop_reason.unwrap());
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

    /// Check before and after bounded synchronous processing and before business side effects.
    /// Only a check that stops work commits a reason; signaling intent alone does not.
    pub fn checkpoint(&self) -> Result<(), LifecycleStopReason> {
        let mut snapshot = self.shared.snapshot();
        let reason = snapshot.stop_reason.or_else(|| {
            if Instant::now() >= self.shared.deadline {
                Some(LifecycleStopReason::DeadlineExceeded)
            } else {
                *self.cancellation.signal.borrow()
            }
        });
        if let Some(reason) = reason {
            snapshot.stop_reason = Some(reason);
            Err(reason)
        } else {
            Ok(())
        }
    }

    /// Bound a complete stage, including pool/connection waits and all response framing.
    /// The future is owned inline and dropped before this method returns on a stop.
    pub async fn run<F: Future>(&self, work: F) -> Result<F::Output, LifecycleStopReason> {
        self.checkpoint()?;
        let mut stage = ActiveStage {
            lifecycle: self,
            finished: false,
        };
        let result = tokio::select! {
            biased;
            _ = self.cancellation.cancelled() => Err(self.checkpoint().unwrap_err()),
            _ = tokio::time::sleep_until(self.shared.deadline) => Err(self.checkpoint().unwrap_err()),
            result = work => Ok(result),
        };
        self.checkpoint()?;
        stage.finished = true;
        result
    }
}

// An outer owner can drop a pending stage before its select branch is polled again.
// Preserve a known stop even during ingress, where no Execution guard exists yet.
struct ActiveStage<'a> {
    lifecycle: &'a RequestLifecycle,
    finished: bool,
}

impl Drop for ActiveStage<'_> {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.lifecycle.checkpoint();
        }
    }
}

/// Shared cancellation intent. Dropping a handle does not cancel work.
#[derive(Clone)]
pub struct RequestCancellation {
    signal: watch::Sender<Option<LifecycleStopReason>>,
}

impl RequestCancellation {
    pub fn cancel(&self) {
        self.cancel_with(LifecycleStopReason::RequestCancelled);
    }

    /// Signal a known lifecycle cause, including service shutdown. The first intent wins.
    pub fn cancel_with(&self, reason: LifecycleStopReason) {
        self.signal.send_if_modified(|current| {
            if current.is_some() {
                return false;
            }
            *current = Some(reason);
            true
        });
    }

    pub fn is_cancelled(&self) -> bool {
        self.signal.borrow().is_some()
    }

    pub async fn cancelled(&self) {
        let mut receiver = self.signal.subscribe();
        let _ = receiver.wait_for(Option::is_some).await;
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
    pub(crate) fn lifecycle(&self) -> RequestLifecycle {
        self.control.lifecycle()
    }

    pub(crate) fn checkpoint(&self) -> Result<(), ManagedOutcome> {
        self.control
            .lifecycle
            .checkpoint()
            .map_err(|reason| ManagedOutcome::Stopped {
                reason,
                model_send: self.control.observation().snapshot().model_send,
            })
    }

    pub(crate) fn model_send(&mut self, state: ModelSendState) {
        self.control.lifecycle.shared.snapshot().model_send = state;
    }

    pub(crate) fn finish(&mut self, outcome: &ManagedOutcome) {
        self.control.lifecycle.shared.snapshot().execution =
            ExecutionState::Finished(outcome.terminal());
    }
}

impl Drop for Execution {
    fn drop(&mut self) {
        let reason = if self.control.observation().snapshot().execution == ExecutionState::Running {
            self.control.lifecycle.checkpoint().err()
        } else {
            None
        };
        let mut snapshot = self.control.lifecycle.shared.snapshot();
        if snapshot.execution == ExecutionState::Running {
            snapshot.execution = ExecutionState::Finished(
                reason.map_or(ExecutionTerminal::Abandoned, ExecutionTerminal::Stopped),
            );
        }
    }
}
