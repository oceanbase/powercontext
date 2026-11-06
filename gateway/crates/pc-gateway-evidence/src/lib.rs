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

//! Bounded, body-free execution evidence. This is best-effort telemetry, not a ledger.

mod output;
pub use output::{EvidenceStats, JsonEvidenceSink};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UsageCompleteness {
    Known,
    Estimated,
    #[default]
    Unknown,
    Reconciled,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TokenUsage {
    pub tokens: Option<u64>,
    pub completeness: UsageCompleteness,
}

impl TokenUsage {
    fn observed(value: Option<&serde_json::Value>) -> Self {
        match value.and_then(serde_json::Value::as_u64) {
            Some(tokens) => Self {
                tokens: Some(tokens),
                completeness: UsageCompleteness::Known,
            },
            None => Self::default(),
        }
    }
}

/// OpenAI input counts INCLUDE cache reads. Cache counts are subsets, never added to input.
/// No inference from totals, no zero-filling, no Anthropic/other-provider guessing.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct UsageEvidence {
    pub input: TokenUsage,
    pub output: TokenUsage,
    pub cache_read: TokenUsage,
    pub cache_write: TokenUsage,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UsageMapping {
    OpenAiChat,
    OpenAiResponses,
}

impl UsageEvidence {
    pub fn openai(raw: Option<&serde_json::Value>, mapping: UsageMapping) -> Self {
        let Some(raw) = raw else {
            return Self::default();
        };
        let (input, output, details) = match mapping {
            UsageMapping::OpenAiChat => (
                "prompt_tokens",
                "completion_tokens",
                "prompt_tokens_details",
            ),
            UsageMapping::OpenAiResponses => {
                ("input_tokens", "output_tokens", "input_tokens_details")
            }
        };
        Self {
            input: TokenUsage::observed(raw.get(input)),
            output: TokenUsage::observed(raw.get(output)),
            cache_read: TokenUsage::observed(raw.get(details).and_then(|v| v.get("cached_tokens"))),
            // These OpenAI contracts expose no cache-write token count.
            cache_write: TokenUsage::default(),
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Sending {
    #[default]
    NoSend,
    DefinitelyNotSent,
    PossiblySent,
    ResponseReceived,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Terminal {
    ReleaseAuthorized,
    Blocked,
    Withheld,
    SendIndeterminate,
    NotSent,
    GatePaused,
    Degraded,
    AdmissionExpired,
    #[default]
    Abandoned,
}

/// Facts observed on future drop; neither intent nor elapsed deadline proves its cause.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Interruption {
    CancellationRequested,
    DeadlineElapsed,
    FutureDropped,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CallEvidence {
    pub call_id: String,
    /// Process-local keyed fingerprint of the bound request ID; absent without host correlation.
    pub request_id_fingerprint: Option<String>,
    /// Fingerprint of the actual transport URL. Never a URL, profile, model or credential.
    pub target_fingerprint: String,
    pub usage_mapping: Option<UsageMapping>,
    pub terminal: Terminal,
    pub sending: Sending,
    pub interruption: Option<Interruption>,
    pub attempts: u32,
    pub usage: UsageEvidence,
    pub upstream_status: Option<u16>,
    /// Elapsed from call start (monotonic milliseconds).
    pub duration_ms: u64,
    pub release_decision_ms: Option<u64>,
    pub release_authorized_ms: Option<u64>,
    /// Unavailable with the current fully buffered transport, including non-streaming Responses.
    pub upstream_ttft_ms: Option<u64>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AttemptOutcome {
    #[default]
    PossiblySent,
    Delivered,
    UpstreamError,
    ConnectRefused,
    RejectedLocally,
    LostAfterHandoff,
    TimeoutAwaitingResponse,
    BufferLimitExceeded,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AttemptEvidence {
    pub call_id: String,
    pub attempt: u32,
    pub outcome: AttemptOutcome,
    /// Milliseconds from request-control creation to the executor's transport handoff.
    pub started_ms: u64,
    /// Executor handoff to transport result or future destruction, in milliseconds.
    pub duration_ms: u64,
    /// Transport's own send start (before awaiting headers) to first nonempty HTTP body chunk.
    /// Its origin differs from `started_ms`; adding them does not give an exact call-relative timestamp.
    pub response_first_byte_ms: Option<u64>,
    /// Same transport send-start origin as `response_first_byte_ms`, to complete buffered response.
    pub response_complete_ms: Option<u64>,
    pub upstream_status: Option<u16>,
    pub usage: UsageEvidence,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryBoundary {
    FrameworkHandoff,
    /// First nonempty HTTP data frame polled by the framework; may contain only SSE control events.
    FirstBodyFrame,
    BodyCompleted,
    BodyDropped,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeliveryEvidence {
    pub call_id: String,
    pub boundary: DeliveryBoundary,
    pub elapsed_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "event", content = "evidence", rename_all = "snake_case")]
pub enum EvidenceEvent {
    Call(CallEvidence),
    Attempt(AttemptEvidence),
    Delivery(DeliveryEvidence),
}

/// Synchronous submissions MUST be bounded, nonblocking and non-I/O.
/// Use JsonEvidenceSink to isolate arbitrary I/O on a dedicated bounded worker.
pub trait EvidenceSink: Send + Sync {
    // Legacy test/demo diagnostics. Not exported by the production sink.
    fn record_attempt(&self, _attempt: u32) {}
    fn record_not_sent(&self, _attempt: u32, _reason: &str) {}
    fn record_call(&self, evidence: &CallEvidence);
    fn record_attempt_evidence(&self, _evidence: &AttemptEvidence) {}
    fn record_delivery(&self, _evidence: &DeliveryEvidence) {}
}

#[derive(Debug, Default, Clone, Copy)]
pub struct NoopEvidenceSink;
impl EvidenceSink for NoopEvidenceSink {
    fn record_call(&self, _evidence: &CallEvidence) {}
}

/// Unbounded test/demo collector; never compose this into a production host.
#[derive(Debug, Default)]
pub struct BufferedEvidenceSink {
    attempts: std::sync::Mutex<Vec<u32>>,
    not_sent: std::sync::Mutex<Vec<(u32, String)>>,
    calls: std::sync::Mutex<Vec<CallEvidence>>,
    events: std::sync::Mutex<Vec<EvidenceEvent>>,
}
impl BufferedEvidenceSink {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn attempts(&self) -> Vec<u32> {
        self.attempts.lock().unwrap().clone()
    }
    pub fn not_sent(&self) -> Vec<(u32, String)> {
        self.not_sent.lock().unwrap().clone()
    }
    pub fn calls(&self) -> Vec<CallEvidence> {
        self.calls.lock().unwrap().clone()
    }
    pub fn events(&self) -> Vec<EvidenceEvent> {
        self.events.lock().unwrap().clone()
    }
}
impl EvidenceSink for BufferedEvidenceSink {
    fn record_attempt(&self, attempt: u32) {
        self.attempts.lock().unwrap().push(attempt);
    }
    fn record_not_sent(&self, attempt: u32, reason: &str) {
        self.not_sent
            .lock()
            .unwrap()
            .push((attempt, reason.to_owned()));
    }
    fn record_call(&self, evidence: &CallEvidence) {
        self.calls.lock().unwrap().push(evidence.clone());
        self.events
            .lock()
            .unwrap()
            .push(EvidenceEvent::Call(evidence.clone()));
    }
    fn record_attempt_evidence(&self, evidence: &AttemptEvidence) {
        self.events
            .lock()
            .unwrap()
            .push(EvidenceEvent::Attempt(evidence.clone()));
    }
    fn record_delivery(&self, evidence: &DeliveryEvidence) {
        self.events
            .lock()
            .unwrap()
            .push(EvidenceEvent::Delivery(evidence.clone()));
    }
}

/// Keyed, process-local correlation avoids exporting arbitrary host strings or URLs.
/// Not a durable identity or cryptographic anonymization guarantee.
pub fn fingerprint(value: &str) -> String {
    use std::hash::{BuildHasher, Hasher};
    static KEY: std::sync::OnceLock<std::collections::hash_map::RandomState> =
        std::sync::OnceLock::new();
    let mut hasher = KEY.get_or_init(Default::default).build_hasher();
    hasher.write(value.as_bytes());
    format!("{:016x}", hasher.finish())
}

pub fn new_call_id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    static INSTANCE: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    let instance = INSTANCE.get_or_init(|| fingerprint("gateway-evidence-instance"));
    format!(
        "{instance}-{:016x}",
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    )
}
