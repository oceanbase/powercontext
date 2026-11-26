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

use crate::{metrics::Metrics, otlp, proxy::SharedProxy};
use axum::{
    extract::State,
    http::{header, HeaderMap},
    response::IntoResponse,
};
use pc_gateway_evidence::{
    AttemptEvidence, AttemptOutcome, CallEvidence, CallStage, DeliveryBoundary, DeliveryEvidence,
    EvidenceSink, PrepareResult, Sending, Terminal, UsageCompleteness,
};
use std::{
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Mutex, MutexGuard, OnceLock,
    },
    time::Instant,
};

#[derive(Default)]
pub(crate) struct Telemetry {
    metrics: Mutex<Metrics>,
    in_flight: AtomicUsize,
    admitted: AtomicUsize,
    exporter: Option<otlp::Exporter>,
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|error| error.into_inner())
}

impl Telemetry {
    pub(crate) fn new(exporter: Option<otlp::Exporter>) -> Self {
        Self {
            exporter,
            ..Self::default()
        }
    }

    pub(crate) fn call(
        self: &Arc<Self>,
        evidence: Arc<dyn EvidenceSink>,
        headers: &HeaderMap,
        route: &'static str,
        call_id: &str,
    ) -> Arc<ModelCall> {
        self.in_flight.fetch_add(1, Ordering::Relaxed);
        let trace = self
            .exporter
            .as_ref()
            .and_then(|_| otlp::Trace::new(headers, call_id, Instant::now()));
        Arc::new(ModelCall {
            telemetry: self.clone(),
            evidence,
            route,
            call_id: call_id.to_owned(),
            trace,
            turn: OnceLock::new(),
            admitted: AtomicBool::new(false),
            blocked_by_core: AtomicBool::new(false),
            completed: OnceLock::new(),
            phases: Mutex::new(Vec::new()),
            prepare_result: Mutex::new(None),
            delivery: OnceLock::new(),
        })
    }
}

pub(crate) async fn scrape(State((state, _)): State<SharedProxy>) -> impl IntoResponse {
    let telemetry = &state.telemetry;
    let stats = state.evidence.evidence_stats().unwrap_or_default();
    let metrics = lock(&telemetry.metrics).clone();
    let text = metrics.render(
        telemetry.in_flight.load(Ordering::Relaxed),
        telemetry.admitted.load(Ordering::Relaxed),
        stats.dropped,
        stats.write_failed,
    );
    (
        [(
            header::CONTENT_TYPE,
            "application/openmetrics-text; version=1.0.0; charset=utf-8",
        )],
        text,
    )
}

pub(crate) struct ModelCall {
    telemetry: Arc<Telemetry>,
    evidence: Arc<dyn EvidenceSink>,
    route: &'static str,
    call_id: String,
    trace: Option<otlp::Trace>,
    turn: OnceLock<&'static str>,
    admitted: AtomicBool,
    blocked_by_core: AtomicBool,
    completed: OnceLock<(&'static str, u32, u64)>,
    phases: Mutex<Vec<otlp::Phase>>,
    prepare_result: Mutex<Option<&'static str>>,
    delivery: OnceLock<Instant>,
}

impl ModelCall {
    pub(crate) fn classify(&self, turn: &'static str) {
        let _ = self.turn.set(turn);
    }

    fn turn_kind(&self) -> &'static str {
        self.turn.get().copied().unwrap_or("empty")
    }

    fn phase(
        &self,
        name: &'static str,
        start: Instant,
        end: Instant,
        result: Option<&'static str>,
    ) {
        if self.trace.is_some() {
            // Keep memory bounded even with many configured deterministic retries.
            let mut phases = lock(&self.phases);
            if phases.len() < 64 {
                phases.push(otlp::Phase {
                    name,
                    start,
                    end,
                    result,
                });
            }
        }
    }
}

impl Drop for ModelCall {
    fn drop(&mut self) {
        self.telemetry.in_flight.fetch_sub(1, Ordering::Relaxed);
        if self.admitted.load(Ordering::Relaxed) {
            self.telemetry.admitted.fetch_sub(1, Ordering::Relaxed);
        }
        if let (Some(exporter), Some(trace), Some((terminal, attempts, duration))) =
            (&self.telemetry.exporter, &self.trace, self.completed.get())
        {
            let attributes = [
                otlp::attribute("call_id", &self.call_id),
                otlp::attribute("route", self.route),
                otlp::attribute("turn_kind", self.turn_kind()),
                otlp::attribute("terminal", terminal),
                otlp::attribute("attempt_count", &attempts.to_string()),
                otlp::attribute("duration_ms", &duration.to_string()),
            ];
            exporter.enqueue(trace.payload(&self.call_id, &lock(&self.phases), &attributes));
        }
    }
}

impl EvidenceSink for ModelCall {
    fn record_attempt(&self, attempt: u32) {
        self.evidence.record_attempt(attempt);
    }

    fn record_not_sent(&self, attempt: u32, reason: &str) {
        self.evidence.record_not_sent(attempt, reason);
    }

    fn record_call(&self, call: &CallEvidence) {
        self.evidence.record_call(call);
        let terminal = match call.terminal {
            Terminal::ReleaseAuthorized => "release_authorized",
            Terminal::Blocked => "blocked",
            Terminal::Withheld => "withheld",
            Terminal::SendIndeterminate => "send_indeterminate",
            Terminal::NotSent => "not_sent",
            Terminal::GatePaused => "gate_paused",
            Terminal::Degraded => "degraded",
            Terminal::AdmissionExpired => "admission_expired",
            Terminal::Stopped(_) => "stopped",
            Terminal::Abandoned => "abandoned",
        };
        let outcome = match call.sending {
            Sending::NoSend => "no_send",
            Sending::DefinitelyNotSent => "definitely_not_sent",
            Sending::PossiblySent => "possibly_sent",
            Sending::ResponseReceived
                if call.upstream_status.is_some_and(|status| status >= 400) =>
            {
                "upstream_error"
            }
            Sending::ResponseReceived => "response_received",
        };
        let _ = self
            .completed
            .set((terminal, call.attempts, call.duration_ms));

        let mut metrics = lock(&self.telemetry.metrics);
        metrics.increment(
            "calls",
            format!(
                "{{route=\"{}\",turn_kind=\"{}\",terminal=\"{terminal}\",outcome=\"{outcome}\"}}",
                self.route,
                self.turn_kind()
            ),
            1,
        );
        let exemplar = self.trace.as_ref().map(|trace| trace.id.as_str());
        metrics.observe("call_duration_ms", call.duration_ms, exemplar);
        if let Some(value) = call.release_decision_ms {
            metrics.observe("release_decision_ms", value, None);
        }
        if let Some(value) = call.release_authorized_ms {
            metrics.observe("release_authorized_ms", value, None);
        }
        match call.terminal {
            Terminal::Blocked => {
                let class = if call.release_decision_ms.is_some() {
                    "core_release"
                } else if self.blocked_by_core.load(Ordering::Relaxed) {
                    "core_send"
                } else {
                    "local"
                };
                metrics.increment("blocked", format!("{{reason_class=\"{class}\"}}"), 1);
            }
            Terminal::Degraded => metrics.increment("degraded", String::new(), 1),
            _ => {}
        }
        if call.sending == Sending::PossiblySent {
            metrics.increment("send_indeterminate", String::new(), 1);
        }
        // A call stopped before sending has no provider usage to report as missing.
        if call.sending != Sending::ResponseReceived {
            return;
        }
        for (direction, usage) in [
            ("input", &call.usage.input),
            ("output", &call.usage.output),
            ("cache_read", &call.usage.cache_read),
            ("cache_write", &call.usage.cache_write),
        ] {
            let completeness = match usage.completeness {
                UsageCompleteness::Known => Some("known"),
                UsageCompleteness::Reconciled => Some("reconciled"),
                UsageCompleteness::Unknown | UsageCompleteness::Estimated => None,
            };
            match (usage.tokens, completeness) {
                (Some(tokens), Some(completeness)) => metrics.increment(
                    "tokens",
                    format!("{{direction=\"{direction}\",completeness=\"{completeness}\"}}"),
                    tokens,
                ),
                _ => metrics.increment(
                    "tokens_missing",
                    format!("{{direction=\"{direction}\"}}"),
                    1,
                ),
            }
        }
    }

    fn record_attempt_evidence(&self, attempt: &AttemptEvidence) {
        self.evidence.record_attempt_evidence(attempt);
        let outcome = match attempt.outcome {
            AttemptOutcome::PossiblySent => "possibly_sent",
            AttemptOutcome::Delivered => "delivered",
            AttemptOutcome::UpstreamError => "upstream_error",
            AttemptOutcome::ConnectRefused => "connect_refused",
            AttemptOutcome::RejectedLocally => "rejected_locally",
            AttemptOutcome::LostAfterHandoff => "lost_after_handoff",
            AttemptOutcome::TimeoutAwaitingResponse => "timeout_awaiting_response",
            AttemptOutcome::BufferLimitExceeded => "buffer_limit_exceeded",
        };
        let mut metrics = lock(&self.telemetry.metrics);
        metrics.increment(
            "attempts",
            format!("{{route=\"{}\",attempt_outcome=\"{outcome}\"}}", self.route),
            1,
        );
        metrics.observe("attempt_duration_ms", attempt.duration_ms, None);
        if let Some(value) = attempt.response_first_byte_ms {
            metrics.observe("response_first_byte_ms", value, None);
        }
    }

    fn record_delivery(&self, delivery: &DeliveryEvidence) {
        self.evidence.record_delivery(delivery);
        match delivery.boundary {
            DeliveryBoundary::FrameworkHandoff => {
                let _ = self.delivery.set(Instant::now());
            }
            DeliveryBoundary::BodyCompleted | DeliveryBoundary::BodyDropped => {
                if let Some(start) = self.delivery.get() {
                    let result = if matches!(delivery.boundary, DeliveryBoundary::BodyCompleted) {
                        "body_completed"
                    } else {
                        "body_dropped"
                    };
                    self.phase("delivery", *start, Instant::now(), Some(result));
                }
            }
            _ => {}
        }
    }

    fn record_stage(&self, stage: CallStage, started: Instant) {
        let (name, histogram) = match stage {
            CallStage::Classify => ("classify", None),
            CallStage::Enrich => ("enrich", None),
            CallStage::Prepare => ("prepare", Some("prepare_duration_ms")),
            CallStage::BeforeSend => ("check.before_send", Some("check_before_send_duration_ms")),
            CallStage::BeforeRelease => (
                "check.before_release",
                Some("check_before_release_duration_ms"),
            ),
            CallStage::Upstream => ("upstream.send", None),
        };
        let end = Instant::now();
        if let Some(histogram) = histogram {
            lock(&self.telemetry.metrics).observe(
                histogram,
                end.duration_since(started).as_millis() as u64,
                None,
            );
        }
        let result = (self.trace.is_some() && matches!(stage, CallStage::Enrich))
            .then(|| lock(&self.prepare_result).take())
            .flatten();
        self.phase(name, started, end, result);
    }

    fn record_prepare(&self, result: PrepareResult) {
        let result = match result {
            PrepareResult::Replay => "hit_replay",
            PrepareResult::Prepared => "prepared",
            PrepareResult::Skipped => "skipped",
            PrepareResult::Failed => "failed",
            PrepareResult::Empty => "empty",
        };
        if self.trace.is_some() {
            *lock(&self.prepare_result) = Some(result);
        }
        lock(&self.telemetry.metrics).increment("prepare", format!("{{result=\"{result}\"}}"), 1);
    }

    fn record_context_refresh(&self) {
        lock(&self.telemetry.metrics).increment("context_refresh", String::new(), 1);
    }

    fn record_admission(&self, contexts: usize, bytes: u64) {
        if !self.admitted.swap(true, Ordering::Relaxed) {
            self.telemetry.admitted.fetch_add(1, Ordering::Relaxed);
            let mut metrics = lock(&self.telemetry.metrics);
            metrics.increment("injected_contexts", String::new(), contexts as u64);
            metrics.increment("injected_bytes", String::new(), bytes);
        }
    }

    fn record_core_block(&self) {
        self.blocked_by_core.store(true, Ordering::Relaxed);
    }

    fn record_withhold(&self, retryable: bool) {
        lock(&self.telemetry.metrics).increment(
            "withheld",
            format!("{{retryable=\"{retryable}\"}}"),
            1,
        );
    }
}
