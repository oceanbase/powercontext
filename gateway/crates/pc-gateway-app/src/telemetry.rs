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
    http::{header, HeaderMap, StatusCode},
    response::IntoResponse,
};
use pc_gateway_evidence::{
    AttemptEvidence, AttemptOutcome, CallEvidence, CallStage, DeliveryBoundary, DeliveryEvidence,
    EvidenceSink, PrepareResult, Sending, Terminal, UsageCompleteness,
};
use pc_gateway_types::TurnKind;
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex, MutexGuard, OnceLock,
    },
    time::Instant,
};

pub(crate) struct Telemetry {
    metrics: Metrics,
    exporter: Option<otlp::Exporter>,
}

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|error| error.into_inner())
}

impl Telemetry {
    pub(crate) fn new(evidence: Arc<dyn EvidenceSink>, exporter: Option<otlp::Exporter>) -> Self {
        Self {
            metrics: Metrics::new(evidence),
            exporter,
        }
    }

    pub(crate) fn call(
        self: &Arc<Self>,
        evidence: Arc<dyn EvidenceSink>,
        headers: &HeaderMap,
        route: &'static str,
        call_id: &str,
    ) -> Arc<ModelCall> {
        self.metrics.in_flight.inc();
        let trace = self
            .exporter
            .as_ref()
            .and_then(|exporter| exporter.start(headers, route, call_id));
        Arc::new(ModelCall {
            telemetry: self.clone(),
            evidence,
            route,
            trace,
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
    let text = state
        .telemetry
        .metrics
        .render()
        .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok::<_, StatusCode>((
        [(
            header::CONTENT_TYPE,
            "application/openmetrics-text; version=1.0.0; charset=utf-8",
        )],
        text,
    ))
}

pub(crate) struct ModelCall {
    telemetry: Arc<Telemetry>,
    evidence: Arc<dyn EvidenceSink>,
    route: &'static str,
    trace: Option<otlp::Trace>,
    admitted: AtomicBool,
    blocked_by_core: AtomicBool,
    completed: OnceLock<(&'static str, u32, u64, &'static str)>,
    phases: Mutex<Vec<otlp::Phase>>,
    prepare_result: Mutex<Option<&'static str>>,
    delivery: OnceLock<Instant>,
}

impl ModelCall {
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
        self.telemetry.metrics.in_flight.dec();
        if self.admitted.load(Ordering::Relaxed) {
            self.telemetry.metrics.admitted.dec();
        }
        if let (Some(exporter), Some(trace), Some(&(terminal, attempts, duration, turn_kind))) = (
            &self.telemetry.exporter,
            self.trace.take(),
            self.completed.get(),
        ) {
            exporter.finish(
                trace,
                &lock(&self.phases),
                terminal,
                attempts,
                duration,
                turn_kind,
            );
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
        let turn_kind = call.turn_kind.map(TurnKind::as_str).unwrap_or("empty");
        let _ = self
            .completed
            .set((terminal, call.attempts, call.duration_ms, turn_kind));

        let metrics = &self.telemetry.metrics;
        metrics
            .calls
            .get_or_create(&[
                ("route", self.route),
                ("turn_kind", turn_kind),
                ("terminal", terminal),
                ("outcome", outcome),
            ])
            .inc();
        let exemplar = self
            .trace
            .as_ref()
            .map(|trace| [("trace_id", trace.id().to_string())]);
        metrics
            .call_duration
            .observe(call.duration_ms as f64, exemplar, None);
        if let Some(value) = call.release_decision_ms {
            metrics.release_decision.observe(value as f64);
        }
        if let Some(value) = call.release_authorized_ms {
            metrics.release_authorized.observe(value as f64);
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
                metrics
                    .blocked
                    .get_or_create(&[("reason_class", class)])
                    .inc();
            }
            Terminal::Degraded => {
                metrics.degraded.inc();
            }
            _ => {}
        }
        if call.sending == Sending::PossiblySent {
            metrics.send_indeterminate.inc();
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
                (Some(tokens), Some(completeness)) => {
                    metrics
                        .tokens
                        .get_or_create(&[("direction", direction), ("completeness", completeness)])
                        .inc_by(tokens);
                }
                _ => {
                    metrics
                        .tokens_missing
                        .get_or_create(&[("direction", direction)])
                        .inc();
                }
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
        let metrics = &self.telemetry.metrics;
        metrics
            .attempts
            .get_or_create(&[("route", self.route), ("attempt_outcome", outcome)])
            .inc();
        metrics.attempt_duration.observe(attempt.duration_ms as f64);
        if let Some(value) = attempt.response_first_byte_ms {
            metrics.response_first_byte.observe(value as f64);
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
        let metrics = &self.telemetry.metrics;
        let (name, histogram) = match stage {
            CallStage::Classify => ("classify", None),
            CallStage::Enrich => ("enrich", None),
            CallStage::Prepare => ("prepare", Some(&metrics.prepare_duration)),
            CallStage::BeforeSend => (
                "check.before_send",
                Some(&metrics.check_before_send_duration),
            ),
            CallStage::BeforeRelease => (
                "check.before_release",
                Some(&metrics.check_before_release_duration),
            ),
            CallStage::Upstream => ("upstream.send", None),
        };
        let end = Instant::now();
        if let Some(histogram) = histogram {
            histogram.observe(end.duration_since(started).as_millis() as f64);
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
        self.telemetry
            .metrics
            .prepare
            .get_or_create(&[("result", result)])
            .inc();
    }

    fn record_context_refresh(&self) {
        self.telemetry.metrics.context_refresh.inc();
    }

    fn record_admission(&self, contexts: usize, bytes: u64) {
        if !self.admitted.swap(true, Ordering::Relaxed) {
            let metrics = &self.telemetry.metrics;
            metrics.admitted.inc();
            metrics.injected_contexts.inc_by(contexts as u64);
            metrics.injected_bytes.inc_by(bytes);
        }
    }

    fn record_core_block(&self) {
        self.blocked_by_core.store(true, Ordering::Relaxed);
    }

    fn record_withhold(&self, retryable: bool) {
        let label = if retryable { "true" } else { "false" };
        self.telemetry
            .metrics
            .withheld
            .get_or_create(&[("retryable", label)])
            .inc();
    }
}
