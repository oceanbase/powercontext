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

use axum::http::HeaderMap;
use pc_gateway_evidence::fingerprint;
use serde_json::{json, Value};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;

#[derive(Clone)]
pub(crate) struct Exporter(mpsc::Sender<Value>);

impl Exporter {
    pub(crate) fn from_env() -> Option<Self> {
        if std::env::var("PC_GATEWAY_OTLP").as_deref() != Ok("on") {
            return None;
        }
        let endpoint = std::env::var("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            .or_else(|_| {
                std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT")
                    .map(|base| format!("{}/v1/traces", base.trim_end_matches('/')))
            })
            .ok()?;
        let headers = std::env::var("OTEL_EXPORTER_OTLP_TRACES_HEADERS")
            .or_else(|_| std::env::var("OTEL_EXPORTER_OTLP_HEADERS"))
            .unwrap_or_default();
        Self::new(&endpoint, &headers)
    }

    pub(crate) fn new(endpoint: &str, headers: &str) -> Option<Self> {
        let endpoint = reqwest::Url::parse(endpoint).ok()?;
        if !matches!(endpoint.scheme(), "http" | "https") {
            return None;
        }
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(2))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .ok()?;
        let mut parsed = HeaderMap::new();
        for entry in headers.split(',').filter(|entry| !entry.is_empty()) {
            let (name, value) = entry.trim().split_once('=')?;
            // OTLP header values are percent-encoded. Preserve literal plus signs.
            let decoder = reqwest::Url::parse(&format!(
                "http://localhost/?v={}",
                value.replace('+', "%2B")
            ))
            .ok()?;
            let value = decoder.query_pairs().next()?.1;
            let name = reqwest::header::HeaderName::from_bytes(name.trim().as_bytes()).ok()?;
            let mut value = reqwest::header::HeaderValue::from_str(&value).ok()?;
            value.set_sensitive(true);
            parsed.append(name, value);
        }
        let (sender, mut receiver) = mpsc::channel(128);
        tokio::spawn(async move {
            while let Some(payload) = receiver.recv().await {
                let _ = client
                    .post(endpoint.clone())
                    .headers(parsed.clone())
                    .json(&payload)
                    .send()
                    .await;
            }
        });
        Some(Self(sender))
    }

    pub(crate) fn enqueue(&self, payload: Value) {
        let _ = self.0.try_send(payload);
    }
}

#[derive(Clone, Copy)]
pub(crate) struct Phase {
    pub(crate) name: &'static str,
    pub(crate) start: Instant,
    pub(crate) end: Instant,
    pub(crate) result: Option<&'static str>,
}

pub(crate) struct Trace {
    pub(crate) id: String,
    parent: Option<String>,
    pub(crate) started: Instant,
    unix_start: u128,
}

impl Trace {
    pub(crate) fn new(headers: &HeaderMap, call_id: &str, started: Instant) -> Option<Self> {
        let parent = headers
            .get("traceparent")
            .and_then(|value| value.to_str().ok())
            .and_then(parse_parent);
        if parent.is_some_and(|(_, _, sampled)| !sampled) {
            return None;
        }
        Some(Self {
            id: parent.map_or_else(
                || call_id.replace('-', ""),
                |(trace, _, _)| trace.to_owned(),
            ),
            parent: parent.map(|(_, span, _)| span.to_owned()),
            started,
            unix_start: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .ok()?
                .as_nanos(),
        })
    }

    pub(crate) fn payload(&self, call_id: &str, phases: &[Phase], attributes: &[Value]) -> Value {
        let root = fingerprint(&format!("{call_id}:model_call"));
        let timestamp = |instant: Instant| {
            (self.unix_start + instant.saturating_duration_since(self.started).as_nanos())
                .to_string()
        };
        let mut spans = Vec::with_capacity(phases.len() + 1);
        let mut model_call = json!({
            "traceId": self.id, "spanId": root, "name": "gateway.model_call", "kind": 2,
            "startTimeUnixNano": self.unix_start.to_string(), "endTimeUnixNano": timestamp(Instant::now()),
            "attributes": attributes, "flags": 1,
        });
        if let Some(parent) = &self.parent {
            model_call["parentSpanId"] = json!(parent);
        }
        spans.push(model_call);
        for (index, phase) in phases.iter().enumerate() {
            let mut span = json!({
                "traceId": self.id, "spanId": fingerprint(&format!("{call_id}:{index}")),
                "parentSpanId": root, "name": phase.name, "kind": 1, "flags": 1,
                "startTimeUnixNano": timestamp(phase.start), "endTimeUnixNano": timestamp(phase.end),
            });
            if let Some(result) = phase.result {
                span["attributes"] = json!([attribute("result", result)]);
            }
            spans.push(span);
        }
        json!({"resourceSpans": [{
            "resource": {"attributes": [attribute("service.name", "powercontext-gateway")]},
            "scopeSpans": [{"scope": {"name": "pc-gateway"}, "spans": spans}]
        }]})
    }
}

pub(crate) fn attribute(key: &str, value: &str) -> Value {
    json!({"key": key, "value": {"stringValue": value}})
}

fn parse_parent(value: &str) -> Option<(&str, &str, bool)> {
    let mut parts = value.split('-');
    let (version, trace, parent, flags) =
        (parts.next()?, parts.next()?, parts.next()?, parts.next()?);
    let lower_hex = |s: &str| {
        s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    };
    if version != "00"
        || parts.next().is_some()
        || trace.len() != 32
        || parent.len() != 16
        || flags.len() != 2
        || ![trace, parent, flags].into_iter().all(lower_hex)
        || trace.bytes().all(|b| b == b'0')
        || parent.bytes().all(|b| b == b'0')
    {
        return None;
    }
    Some((trace, parent, u8::from_str_radix(flags, 16).ok()? & 1 == 1))
}
