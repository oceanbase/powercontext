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

use pc_gateway_evidence::EvidenceSink;
use prometheus_client::{
    collector::Collector,
    encoding::{text::encode, DescriptorEncoder, EncodeMetric},
    metrics::{
        counter::{Atomic, ConstCounter, Counter},
        exemplar::HistogramWithExemplars,
        family::Family,
        gauge::Gauge,
        histogram::Histogram,
    },
    registry::Registry,
};
use std::{
    fmt,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
};

const BOUNDS: [u64; 13] = [
    1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 15000, 60000,
];

type Labels<const N: usize> = [(&'static str, &'static str); N];
type Counters<const N: usize> = Family<Labels<N>, Counter>;
type TraceLabels = [(&'static str, String); 1];

/// The Gateway only records fixed metric names and locally classified labels.
pub(crate) struct Metrics {
    registry: Registry,
    pub(crate) in_flight: Gauge,
    pub(crate) admitted: Gauge,
    pub(crate) calls: Counters<4>,
    pub(crate) attempts: Counters<2>,
    pub(crate) blocked: Counters<1>,
    pub(crate) degraded: Counter,
    pub(crate) send_indeterminate: Counter,
    pub(crate) prepare: Counters<1>,
    pub(crate) context_refresh: Counter,
    pub(crate) injected_contexts: Counter,
    pub(crate) injected_bytes: Counter,
    pub(crate) withheld: Counters<1>,
    pub(crate) tokens: Family<Labels<2>, Counter<u64, SaturatingAtomic>>,
    pub(crate) tokens_missing: Counters<1>,
    pub(crate) call_duration: HistogramWithExemplars<TraceLabels>,
    pub(crate) attempt_duration: Histogram,
    pub(crate) response_first_byte: Histogram,
    pub(crate) release_decision: Histogram,
    pub(crate) release_authorized: Histogram,
    pub(crate) prepare_duration: Histogram,
    pub(crate) check_before_send_duration: Histogram,
    pub(crate) check_before_release_duration: Histogram,
}

impl Metrics {
    pub(crate) fn new(evidence: Arc<dyn EvidenceSink>) -> Self {
        let mut registry = Registry::with_prefix("powercontext_gateway");
        registry.register_collector(Box::new(EvidenceCounters(evidence)));
        let buckets = || BOUNDS.into_iter().map(|bound| bound as f64);
        Self {
            in_flight: metric(
                &mut registry,
                "in_flight",
                "Model calls in flight",
                Gauge::default(),
            ),
            admitted: metric(
                &mut registry,
                "admitted_in_flight",
                "Admitted model calls in flight",
                Gauge::default(),
            ),
            calls: metric(
                &mut registry,
                "calls",
                "Completed model calls",
                Counters::default(),
            ),
            attempts: metric(
                &mut registry,
                "attempts",
                "Model delivery attempts",
                Counters::default(),
            ),
            blocked: metric(
                &mut registry,
                "blocked",
                "Blocked model calls",
                Counters::default(),
            ),
            degraded: metric(
                &mut registry,
                "degraded",
                "Degraded model calls",
                Counter::default(),
            ),
            send_indeterminate: metric(
                &mut registry,
                "send_indeterminate",
                "Model calls with indeterminate send",
                Counter::default(),
            ),
            prepare: metric(
                &mut registry,
                "prepare",
                "Context preparation results",
                Counters::default(),
            ),
            context_refresh: metric(
                &mut registry,
                "context_refresh",
                "Context refreshes",
                Counter::default(),
            ),
            injected_contexts: metric(
                &mut registry,
                "injected_contexts",
                "Admitted injected contexts",
                Counter::default(),
            ),
            injected_bytes: metric(
                &mut registry,
                "injected_bytes",
                "Admitted injected context bytes",
                Counter::default(),
            ),
            withheld: metric(
                &mut registry,
                "withheld",
                "Withheld model calls",
                Counters::default(),
            ),
            tokens: metric(
                &mut registry,
                "tokens",
                "Provider-reported tokens",
                Family::default(),
            ),
            tokens_missing: metric(
                &mut registry,
                "tokens_missing",
                "Unavailable provider token measurements",
                Counters::default(),
            ),
            call_duration: metric(
                &mut registry,
                "call_duration_ms",
                "Model call duration in milliseconds",
                HistogramWithExemplars::new(buckets()),
            ),
            attempt_duration: metric(
                &mut registry,
                "attempt_duration_ms",
                "Attempt duration in milliseconds",
                Histogram::new(buckets()),
            ),
            response_first_byte: metric(
                &mut registry,
                "response_first_byte_ms",
                "Time to first upstream response byte in milliseconds",
                Histogram::new(buckets()),
            ),
            release_decision: metric(
                &mut registry,
                "release_decision_ms",
                "Time to release decision in milliseconds",
                Histogram::new(buckets()),
            ),
            release_authorized: metric(
                &mut registry,
                "release_authorized_ms",
                "Time to release authorization in milliseconds",
                Histogram::new(buckets()),
            ),
            prepare_duration: metric(
                &mut registry,
                "prepare_duration_ms",
                "Context preparation duration in milliseconds",
                Histogram::new(buckets()),
            ),
            check_before_send_duration: metric(
                &mut registry,
                "check_before_send_duration_ms",
                "Before-send check duration in milliseconds",
                Histogram::new(buckets()),
            ),
            check_before_release_duration: metric(
                &mut registry,
                "check_before_release_duration_ms",
                "Before-release check duration in milliseconds",
                Histogram::new(buckets()),
            ),
            registry,
        }
    }

    pub(crate) fn render(&self) -> Result<String, fmt::Error> {
        let mut text = String::new();
        encode(&mut text, &self.registry)?;
        Ok(text)
    }
}

fn metric<M: prometheus_client::registry::Metric + Clone>(
    registry: &mut Registry,
    name: &str,
    help: &str,
    metric: M,
) -> M {
    registry.register(name, help, metric.clone());
    metric
}

struct EvidenceCounters(Arc<dyn EvidenceSink>);

impl fmt::Debug for EvidenceCounters {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EvidenceCounters")
            .finish_non_exhaustive()
    }
}

impl Collector for EvidenceCounters {
    fn encode(&self, mut encoder: DescriptorEncoder<'_>) -> Result<(), fmt::Error> {
        let stats = self.0.evidence_stats().unwrap_or_default();
        for (name, help, value) in [
            (
                "evidence_dropped",
                "Evidence records dropped",
                stats.dropped,
            ),
            (
                "evidence_write_failed",
                "Evidence write failures",
                stats.write_failed,
            ),
        ] {
            let counter = ConstCounter::new(value);
            counter.encode(encoder.encode_descriptor(
                name,
                help,
                None,
                counter.metric_type(),
            )?)?;
        }
        Ok(())
    }
}

/// Provider totals can be arbitrarily large. Keep the counter monotonic on overflow.
#[derive(Debug, Default)]
pub(crate) struct SaturatingAtomic(AtomicU64);

impl Atomic<u64> for SaturatingAtomic {
    fn inc(&self) -> u64 {
        self.inc_by(1)
    }

    fn inc_by(&self, value: u64) -> u64 {
        self.0
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |count| {
                Some(count.saturating_add(value))
            })
            .unwrap_or_else(std::convert::identity)
    }

    fn get(&self) -> u64 {
        self.0.load(Ordering::Relaxed)
    }
}
