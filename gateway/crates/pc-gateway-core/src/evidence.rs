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

//! Execution-owned evidence, including destruction of a pending future.
use crate::{ExecutionObservation, ManagedOutcome, RequestCancellation};
use pc_gateway_evidence::{
    fingerprint, AttemptEvidence, AttemptOutcome, CallEvidence, EvidenceSink, Interruption,
    Sending, Terminal, UsageEvidence, UsageMapping,
};
use pc_gateway_transport::{NotSentReason, RawResponse, SendIndeterminateReason};
use std::time::Instant;

pub(crate) struct CallRecorder<'a> {
    sink: &'a dyn EvidenceSink,
    observation: ExecutionObservation,
    cancellation: RequestCancellation,
    call: CallEvidence,
    attempt: Option<(AttemptEvidence, Instant)>,
}

impl<'a> CallRecorder<'a> {
    pub(crate) fn new(
        sink: &'a dyn EvidenceSink,
        observation: ExecutionObservation,
        cancellation: RequestCancellation,
        target: &str,
        mapping: Option<UsageMapping>,
    ) -> Self {
        let call = CallEvidence {
            call_id: observation.call_id().to_owned(),
            request_id_fingerprint: observation.request_id().map(fingerprint),
            target_fingerprint: fingerprint(target),
            usage_mapping: mapping,
            ..Default::default()
        };
        Self {
            sink,
            observation,
            cancellation,
            call,
            attempt: None,
        }
    }

    pub(crate) fn begin_attempt(&mut self) {
        self.call.attempts += 1;
        self.call.sending = Sending::PossiblySent;
        self.attempt = Some((
            AttemptEvidence {
                call_id: self.call.call_id.clone(),
                attempt: self.call.attempts,
                started_ms: self.observation.elapsed_ms(),
                ..Default::default()
            },
            Instant::now(),
        ));
    }

    pub(crate) fn received(&mut self, raw: &RawResponse) {
        self.call.sending = Sending::ResponseReceived;
        self.call.upstream_status = Some(raw.status);
        self.call.usage = self
            .call
            .usage_mapping
            .map(|mapping| UsageEvidence::openai(raw.usage.as_ref(), mapping))
            .unwrap_or_default();
        if let Some((attempt, _)) = &mut self.attempt {
            attempt.outcome = if raw.status >= 400 {
                AttemptOutcome::UpstreamError
            } else {
                AttemptOutcome::Delivered
            };
            attempt.upstream_status = Some(raw.status);
            attempt.response_first_byte_ms = raw.response_first_byte_ms;
            attempt.response_complete_ms = raw.response_complete_ms;
            attempt.usage = self.call.usage.clone();
        }
        self.finish_attempt();
    }

    pub(crate) fn not_sent(&mut self, reason: &NotSentReason) {
        self.call.sending = Sending::DefinitelyNotSent;
        if let Some((attempt, _)) = &mut self.attempt {
            attempt.outcome = match reason {
                NotSentReason::ConnectRefused => AttemptOutcome::ConnectRefused,
                NotSentReason::RejectedLocally => AttemptOutcome::RejectedLocally,
            };
        }
        self.finish_attempt();
    }

    pub(crate) fn incomplete_response(
        &mut self,
        reason: &SendIndeterminateReason,
        status: u16,
        first_byte: Option<u64>,
    ) {
        self.call.upstream_status = Some(status);
        if let Some((attempt, _)) = &mut self.attempt {
            attempt.upstream_status = Some(status);
            attempt.response_first_byte_ms = first_byte;
        }
        self.indeterminate(reason);
    }

    pub(crate) fn indeterminate(&mut self, reason: &SendIndeterminateReason) {
        if let Some((attempt, _)) = &mut self.attempt {
            attempt.outcome = match reason {
                SendIndeterminateReason::LostAfterHandoff => AttemptOutcome::LostAfterHandoff,
                SendIndeterminateReason::TimeoutAwaitingResponse => {
                    AttemptOutcome::TimeoutAwaitingResponse
                }
                SendIndeterminateReason::BufferLimitExceeded => AttemptOutcome::BufferLimitExceeded,
            };
        }
        self.finish_attempt();
    }

    fn finish_attempt(&mut self) {
        if let Some((mut attempt, start)) = self.attempt.take() {
            attempt.duration_ms = start.elapsed().as_millis() as u64;
            self.sink.record_attempt_evidence(&attempt);
        }
    }

    pub(crate) fn release_decision(&mut self) {
        self.call.release_decision_ms = Some(self.observation.elapsed_ms());
    }
    pub(crate) fn authorized(&mut self) {
        self.call.release_authorized_ms = Some(self.observation.elapsed_ms());
    }

    pub(crate) fn finish(&mut self, outcome: &ManagedOutcome) {
        self.call.terminal = match outcome {
            ManagedOutcome::Released { .. } => Terminal::ReleaseAuthorized,
            ManagedOutcome::Blocked { .. } => Terminal::Blocked,
            ManagedOutcome::Withheld { .. } => Terminal::Withheld,
            ManagedOutcome::SendIndeterminate => Terminal::SendIndeterminate,
            ManagedOutcome::NotSent { .. } => Terminal::NotSent,
            ManagedOutcome::GatePaused(_) => Terminal::GatePaused,
            ManagedOutcome::Degraded { .. } => Terminal::Degraded,
            ManagedOutcome::AdmissionExpired => Terminal::AdmissionExpired,
        };
    }
}

impl Drop for CallRecorder<'_> {
    fn drop(&mut self) {
        self.finish_attempt();
        if self.call.terminal == Terminal::Abandoned {
            self.call.interruption = Some(if self.cancellation.is_cancelled() {
                Interruption::CancellationRequested
            } else if tokio::time::Instant::now() >= self.observation.deadline() {
                Interruption::DeadlineElapsed
            } else {
                Interruption::FutureDropped
            });
        }
        self.call.duration_ms = self.observation.elapsed_ms();
        self.sink.record_call(&self.call);
    }
}
