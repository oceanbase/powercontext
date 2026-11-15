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

use crate::{
    AttemptEvidence, AttemptOutcome, CallEvidence, DeliveryEvidence, EvidenceEvent, EvidenceSink,
    Terminal,
};
use serde::Serialize;
use std::io::Write;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc};

// Match the Server's bounded operational vocabulary without discarding domain facts.
#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum OperationalOutcome {
    Success,
    Failure,
    Blocked,
}

fn operational_outcome(event: &EvidenceEvent) -> Option<OperationalOutcome> {
    use OperationalOutcome::*;
    match event {
        EvidenceEvent::Call(call) => match call.terminal {
            Terminal::ReleaseAuthorized | Terminal::Degraded => call
                .upstream_status
                .map(|status| if status >= 400 { Failure } else { Success }),
            Terminal::Blocked | Terminal::Withheld | Terminal::AdmissionExpired => Some(Blocked),
            Terminal::SendIndeterminate
            | Terminal::NotSent
            | Terminal::GatePaused
            | Terminal::Stopped(_) => Some(Failure),
            // Drop-time intent/deadline facts do not establish why the owner stopped execution.
            Terminal::Abandoned => None,
        },
        EvidenceEvent::Attempt(attempt) => match attempt.outcome {
            AttemptOutcome::Delivered => Some(Success),
            AttemptOutcome::PossiblySent => None,
            AttemptOutcome::UpstreamError
            | AttemptOutcome::ConnectRefused
            | AttemptOutcome::RejectedLocally
            | AttemptOutcome::LostAfterHandoff
            | AttemptOutcome::TimeoutAwaitingResponse
            | AttemptOutcome::BufferLimitExceeded => Some(Failure),
        },
        EvidenceEvent::Delivery(_) => None,
    }
}

#[derive(Debug, Clone, Copy, Default, Serialize)]
pub struct EvidenceStats {
    pub dropped: u64,
    pub write_failed: u64,
    pub written: u64,
}

#[derive(Default)]
struct Counters {
    dropped: AtomicU64,
    write_failed: AtomicU64,
    written: AtomicU64,
}
impl Counters {
    fn snapshot(&self) -> EvidenceStats {
        EvidenceStats {
            dropped: self.dropped.load(Ordering::Relaxed),
            write_failed: self.write_failed.load(Ordering::Relaxed),
            written: self.written.load(Ordering::Relaxed),
        }
    }
}

/// Fixed-capacity, drop-newest JSON-lines exporter. One worker owns all blocking writes.
/// Drop never joins a possibly blocked writer. At process exit pending records can be lost.
/// A record is at most 4096 serialized bytes; at most capacity queued records plus one in flight.
pub struct JsonEvidenceSink {
    sender: mpsc::SyncSender<EvidenceEvent>,
    counters: Arc<Counters>,
}

impl JsonEvidenceSink {
    pub fn new(
        writer: impl Write + Send + 'static,
        capacity: std::num::NonZeroUsize,
    ) -> std::io::Result<Self> {
        let (sender, receiver) = mpsc::sync_channel::<EvidenceEvent>(capacity.get());
        let counters = Arc::new(Counters::default());
        let worker_counters = counters.clone();
        std::thread::Builder::new()
            .name("gateway-evidence".into())
            .spawn(move || {
                let mut writer = writer;
                let mut recover_line = false;
                for event in receiver {
                    #[derive(Serialize)]
                    struct Envelope<'a> {
                        schema: &'static str,
                        operation: &'static str,
                        unit: &'static str,
                        outcome: Option<OperationalOutcome>,
                        duration_ms: Option<u64>,
                        #[serde(flatten)]
                        event: &'a EvidenceEvent,
                        loss: EvidenceStats,
                    }
                    let envelope = Envelope {
                        schema: "powercontext.gateway-evidence.v1",
                        operation: "gateway.model_call",
                        unit: match event {
                            EvidenceEvent::Call(_) => "call",
                            EvidenceEvent::Attempt(_) => "attempt",
                            EvidenceEvent::Delivery(_) => "delivery",
                        },
                        outcome: operational_outcome(&event),
                        duration_ms: match &event {
                            EvidenceEvent::Call(call) => Some(call.duration_ms),
                            EvidenceEvent::Attempt(attempt) => Some(attempt.duration_ms),
                            EvidenceEvent::Delivery(_) => None,
                        },
                        event: &event,
                        loss: worker_counters.snapshot(),
                    };
                    // Never stringify writer errors. Custom writers must not panic.
                    // After a partial failure, restore a line boundary before the next record.
                    let result = serde_json::to_vec(&envelope)
                        .ok()
                        .filter(|line| line.len() < 4096)
                        .and_then(|mut line| {
                            line.push(b'\n');
                            if recover_line && writer.write_all(b"\n").is_err() {
                                return None;
                            }
                            writer.write_all(&line).and_then(|_| writer.flush()).ok()
                        });
                    recover_line = result.is_none();
                    if result.is_some() {
                        worker_counters.written.fetch_add(1, Ordering::Relaxed);
                    } else {
                        worker_counters.write_failed.fetch_add(1, Ordering::Relaxed);
                    }
                }
            })?;
        Ok(Self { sender, counters })
    }

    pub fn stats(&self) -> EvidenceStats {
        self.counters.snapshot()
    }

    fn submit(&self, event: EvidenceEvent) {
        // The producer supplies only bounded typed records. Reject oversized caller strings
        // before queueing; neither raw fields nor legacy diagnostics are exported.
        let valid_id =
            |id: &str| id.len() <= 64 && id.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-');
        let valid = match &event {
            EvidenceEvent::Call(e) => {
                valid_id(&e.call_id)
                    && valid_id(&e.target_fingerprint)
                    && e.request_id_fingerprint.as_deref().is_none_or(valid_id)
            }
            EvidenceEvent::Attempt(e) => valid_id(&e.call_id),
            EvidenceEvent::Delivery(e) => valid_id(&e.call_id),
        };
        if !valid || self.sender.try_send(event).is_err() {
            self.counters.dropped.fetch_add(1, Ordering::Relaxed);
        }
    }
}
impl EvidenceSink for JsonEvidenceSink {
    fn record_call(&self, evidence: &CallEvidence) {
        self.submit(EvidenceEvent::Call(evidence.clone()));
    }
    fn record_attempt_evidence(&self, evidence: &AttemptEvidence) {
        self.submit(EvidenceEvent::Attempt(evidence.clone()));
    }
    fn record_delivery(&self, evidence: &DeliveryEvidence) {
        self.submit(EvidenceEvent::Delivery(evidence.clone()));
    }
}
