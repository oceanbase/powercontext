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

use axum::http::{HeaderMap, Request, Response};
use bytes::Bytes;
use opentelemetry::{
    propagation::{Extractor, TextMapPropagator},
    trace::{Span as _, TraceContextExt, TraceId, Tracer as _, TracerProvider as _},
    Context, KeyValue,
};
use opentelemetry_http::{HttpClient, HttpError};
use opentelemetry_otlp::{Protocol, WithExportConfig, WithHttpConfig};
use opentelemetry_sdk::{
    error::OTelSdkResult,
    propagation::TraceContextPropagator,
    trace::{
        BatchConfigBuilder, BatchSpanProcessor, Sampler, SdkTracer, SdkTracerProvider, SpanData,
        SpanProcessor,
    },
    Resource,
};
use std::{
    collections::HashMap,
    time::{Duration, Instant, SystemTime},
};
use tracing::{dispatcher, Dispatch, Span};
use tracing_opentelemetry::OpenTelemetrySpanExt;
use tracing_subscriber::prelude::*;

const EXPORT_TIMEOUT: Duration = Duration::from_secs(2);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(3);
const CALL_SPAN: &str = "gateway.model_call";

#[derive(Clone)]
pub(crate) struct Exporter {
    provider: SdkTracerProvider,
    tracer: SdkTracer,
    dispatch: Dispatch,
}

impl Exporter {
    pub(crate) async fn from_env() -> Option<Self> {
        if std::env::var("PC_GATEWAY_OTLP").as_deref() != Ok("on") {
            return None;
        }
        let endpoint = std::env::var("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
            .or_else(|_| {
                std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT")
                    .map(|base| format!("{}/v1/traces", base.trim_end_matches('/')))
            })
            .ok()?;
        Self::new(&endpoint, HashMap::new()).await
    }

    pub(crate) async fn new(endpoint: &str, headers: HashMap<String, String>) -> Option<Self> {
        let endpoint = reqwest::Url::parse(endpoint).ok()?;
        if !matches!(endpoint.scheme(), "http" | "https") {
            return None;
        }

        tokio::task::spawn_blocking(move || {
            let client = reqwest::blocking::Client::builder()
                .timeout(EXPORT_TIMEOUT)
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .ok()?;
            let exporter = opentelemetry_otlp::SpanExporter::builder()
                .with_http()
                .with_protocol(Protocol::HttpJson)
                .with_endpoint(endpoint.as_str())
                .with_timeout(EXPORT_TIMEOUT)
                .with_http_client(PrivateHttpClient(client))
                .with_headers(headers)
                .build()
                .ok()?;
            let batch = BatchSpanProcessor::builder(exporter)
                .with_batch_config(
                    BatchConfigBuilder::default()
                        .with_max_queue_size(128)
                        .with_max_export_batch_size(128)
                        .with_scheduled_delay(Duration::from_millis(200))
                        .build(),
                )
                .build();
            let provider = SdkTracerProvider::builder()
                .with_sampler(Sampler::ParentBased(Box::new(Sampler::AlwaysOn)))
                .with_resource(
                    Resource::builder_empty()
                        .with_service_name("powercontext-gateway")
                        .build(),
                )
                .with_span_processor(CompletedCalls(batch))
                .build();
            let tracer = provider.tracer("pc-gateway");
            // Scope the OTLP layer to the allowlisted call span. Gateway and dependency
            // log events continue to use the process subscriber and never enter OTLP.
            let dispatch = Dispatch::new(
                tracing_subscriber::registry().with(
                    tracing_opentelemetry::layer()
                        .with_tracer(tracer.clone())
                        .with_location(false)
                        .with_level(false)
                        .with_target(false)
                        .with_threads(false)
                        .with_tracked_inactivity(false)
                        .with_context_activation(false),
                ),
            );
            Some(Self {
                provider,
                tracer,
                dispatch,
            })
        })
        .await
        .ok()?
    }

    pub(crate) fn start(
        &self,
        headers: &HeaderMap,
        route: &'static str,
        call_id: &str,
    ) -> Option<Trace> {
        let parent = TraceContextPropagator::new()
            .extract_with_context(&Context::new(), &TraceParent(headers));
        let span = dispatcher::with_default(&self.dispatch, || {
            tracing::info_span!(
                parent: None,
                CALL_SPAN,
                otel.kind = "server",
                call_id,
                route,
                turn_kind = tracing::field::Empty,
                terminal = tracing::field::Empty,
                attempt_count = tracing::field::Empty,
                duration_ms = tracing::field::Empty
            )
        });
        span.set_parent(parent).ok()?;
        let context = span.context();
        let started = Instant::now();
        let wall_start = SystemTime::now();
        context.span().span_context().is_sampled().then_some(Trace {
            span,
            context,
            started,
            wall_start,
        })
    }

    pub(crate) async fn shutdown(&self) {
        let provider = self.provider.clone();
        // SDK shutdown flushes the queue. Wait off the async worker and within the
        // process watchdog's remaining budget after model connections have drained.
        let _ =
            tokio::task::spawn_blocking(move || provider.shutdown_with_timeout(SHUTDOWN_TIMEOUT))
                .await;
    }

    pub(crate) fn finish(
        &self,
        trace: Trace,
        phases: &[Phase],
        terminal: &'static str,
        attempts: u32,
        duration_ms: u64,
        turn_kind: &'static str,
    ) {
        let timestamp =
            |instant: Instant| trace.wall_start + instant.saturating_duration_since(trace.started);
        trace.span.record("terminal", terminal);
        trace.span.record("attempt_count", attempts.to_string());
        trace.span.record("duration_ms", duration_ms.to_string());
        trace.span.record("turn_kind", turn_kind);
        drop(trace.span);

        for phase in phases {
            let mut span = self
                .tracer
                .span_builder(phase.name)
                .with_start_time(timestamp(phase.start))
                .with_end_time(timestamp(phase.end))
                .with_attributes(phase.result.map(|result| KeyValue::new("result", result)))
                .start_with_context(&self.tracer, &trace.context);
            span.end_with_timestamp(timestamp(phase.end));
        }
    }
}

#[derive(Debug)]
struct PrivateHttpClient(reqwest::blocking::Client);

#[async_trait::async_trait]
impl HttpClient for PrivateHttpClient {
    async fn send_bytes(&self, mut request: Request<Bytes>) -> Result<Response<Bytes>, HttpError> {
        // The SDK resolves configured OTLP headers. Keep their values out of any
        // HTTP client diagnostics while preserving them on the wire.
        for value in request.headers_mut().values_mut() {
            value.set_sensitive(true);
        }
        self.0.send_bytes(request).await
    }
}

// Only the W3C trace identity crosses this boundary. Caller-supplied vendor
// tracestate and baggage can contain opaque values and are not forwarded.
struct TraceParent<'a>(&'a HeaderMap);

impl Extractor for TraceParent<'_> {
    fn get(&self, key: &str) -> Option<&str> {
        if key != "traceparent" {
            return None;
        }
        self.0.get(key)?.to_str().ok()
    }

    fn keys(&self) -> Vec<&str> {
        vec!["traceparent"]
    }
}

/// Ingress can stop before the executor records a terminal call. Such requests
/// have no model-call evidence and have never been included in exported traces.
#[derive(Debug)]
struct CompletedCalls(BatchSpanProcessor);

impl SpanProcessor for CompletedCalls {
    fn on_start(&self, span: &mut opentelemetry_sdk::trace::Span, context: &Context) {
        self.0.on_start(span, context);
    }

    fn on_end(&self, span: SpanData) {
        if span.name != CALL_SPAN
            || span
                .attributes
                .iter()
                .any(|entry| entry.key.as_str() == "terminal")
        {
            self.0.on_end(span);
        }
    }

    fn force_flush(&self) -> OTelSdkResult {
        self.0.force_flush()
    }

    fn shutdown_with_timeout(&self, timeout: Duration) -> OTelSdkResult {
        self.0.shutdown_with_timeout(timeout)
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
    span: Span,
    context: Context,
    started: Instant,
    wall_start: SystemTime,
}

impl Trace {
    pub(crate) fn id(&self) -> TraceId {
        self.context.span().span_context().trace_id()
    }
}
