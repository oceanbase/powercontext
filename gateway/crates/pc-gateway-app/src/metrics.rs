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

use std::collections::BTreeMap;
use std::fmt::Write;

const PREFIX: &str = "powercontext_gateway_";
const BOUNDS: [u64; 13] = [
    1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 15000, 60000,
];

#[derive(Clone, Default)]
struct Histogram {
    bins: [u64; BOUNDS.len() + 1],
    exemplars: [Option<(u64, String)>; BOUNDS.len() + 1],
    sum: u64,
}

/// Only fixed metric names and bounded, locally classified labels enter this registry.
#[derive(Clone, Default)]
pub(crate) struct Metrics {
    counters: BTreeMap<&'static str, BTreeMap<String, u64>>,
    histograms: BTreeMap<&'static str, Histogram>,
}

impl Metrics {
    pub(crate) fn increment(&mut self, name: &'static str, labels: String, value: u64) {
        let count = self
            .counters
            .entry(name)
            .or_default()
            .entry(labels)
            .or_default();
        *count = count.saturating_add(value);
    }

    pub(crate) fn observe(&mut self, name: &'static str, value: u64, trace: Option<&str>) {
        let histogram = self.histograms.entry(name).or_default();
        let index = BOUNDS.partition_point(|bound| *bound < value);
        histogram.bins[index] = histogram.bins[index].saturating_add(1);
        histogram.sum = histogram.sum.saturating_add(value);
        if let Some(trace) = trace {
            histogram.exemplars[index] = Some((value, trace.to_owned()));
        }
    }

    pub(crate) fn render(
        mut self,
        in_flight: usize,
        admitted: usize,
        evidence_dropped: u64,
        evidence_failed: u64,
    ) -> String {
        let mut out = String::new();
        for (name, value) in [("in_flight", in_flight), ("admitted_in_flight", admitted)] {
            let _ = writeln!(out, "# TYPE {PREFIX}{name} gauge\n{PREFIX}{name} {value}");
        }
        self.increment("evidence_dropped", String::new(), evidence_dropped);
        self.increment("evidence_write_failed", String::new(), evidence_failed);
        for (name, samples) in self.counters {
            let _ = writeln!(out, "# TYPE {PREFIX}{name} counter");
            for (labels, count) in samples {
                let _ = writeln!(out, "{PREFIX}{name}_total{labels} {count}");
            }
        }
        for (name, histogram) in self.histograms {
            let _ = writeln!(out, "# TYPE {PREFIX}{name} histogram");
            let mut count = 0_u64;
            for (index, bin) in histogram.bins.into_iter().enumerate() {
                count = count.saturating_add(bin);
                let upper = BOUNDS
                    .get(index)
                    .map_or_else(|| "+Inf".into(), u64::to_string);
                let _ = write!(out, "{PREFIX}{name}_bucket{{le=\"{upper}\"}} {count}");
                if let Some((value, trace)) = &histogram.exemplars[index] {
                    let _ = write!(out, " # {{trace_id=\"{trace}\"}} {value}");
                }
                out.push('\n');
            }
            let _ = writeln!(
                out,
                "{PREFIX}{name}_count {count}\n{PREFIX}{name}_sum {}",
                histogram.sum
            );
        }
        out.push_str("# EOF\n");
        out
    }
}
