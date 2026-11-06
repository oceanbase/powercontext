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

use pc_gateway_evidence::*;
use std::io::{self, Write};
use std::num::NonZeroUsize;
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Clone, Default)]
struct Output(Arc<Mutex<Vec<u8>>>);
impl Write for Output {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
fn record() -> CallEvidence {
    CallEvidence {
        call_id: new_call_id(),
        target_fingerprint: fingerprint("https://secret:credential@host/private"),
        ..Default::default()
    }
}
fn until(mut condition: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(3);
    while !condition() {
        assert!(Instant::now() < end, "export did not complete");
        std::thread::sleep(Duration::from_millis(1));
    }
}

#[test]
fn readable_output_preserves_unknown_and_separates_cache_usage() {
    let output = Output::default();
    let sink = JsonEvidenceSink::new(output.clone(), NonZeroUsize::new(2).unwrap()).unwrap();
    let mut call = record();
    call.usage = UsageEvidence::openai(
        Some(&serde_json::json!({
            "input_tokens": 100, "output_tokens": 7, "input_tokens_details": {"cached_tokens": 60},
            "cache_creation_input_tokens": 999, "secret": "SYNTHETIC_SENSITIVE_USAGE"
        })),
        UsageMapping::OpenAiResponses,
    );
    call.request_id_fingerprint = Some(fingerprint("SYNTHETIC_SECRET_REQUEST_ID"));
    sink.record_call(&call);
    sink.record_not_sent(0, "SYNTHETIC_SECRET_PROVIDER_EXCEPTION");
    until(|| sink.stats().written == 1);
    let bytes = output.0.lock().unwrap().clone();
    let text = String::from_utf8(bytes).unwrap();
    for marker in [
        "SYNTHETIC",
        "credential",
        "/private",
        "cache_creation_input_tokens",
    ] {
        assert!(!text.contains(marker));
    }
    let value: serde_json::Value = serde_json::from_str(text.trim()).unwrap();
    assert_eq!(value["schema"], "powercontext.gateway-evidence.v1");
    assert_eq!(value["evidence"]["usage"]["input"]["tokens"], 100);
    assert_eq!(value["evidence"]["usage"]["cache_read"]["tokens"], 60);
    assert_eq!(
        value["evidence"]["usage"]["cache_write"]["completeness"],
        "unknown"
    );
    assert!(value["evidence"]["usage"]["cache_write"]["tokens"].is_null());
    assert!(value["evidence"]["upstream_ttft_ms"].is_null());
    assert_eq!(
        UsageEvidence::openai(None, UsageMapping::OpenAiChat),
        UsageEvidence::default()
    );
}

struct SlowWriter {
    started: mpsc::Sender<()>,
    resume: mpsc::Receiver<()>,
    output: Output,
    first: bool,
}
impl Write for SlowWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if self.first {
            self.first = false;
            self.started.send(()).unwrap();
            self.resume.recv().unwrap();
        }
        self.output.write(bytes)
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn blocked_writer_keeps_a_fixed_budget_and_reports_overflow() {
    let (started_tx, started_rx) = mpsc::channel();
    let (resume_tx, resume_rx) = mpsc::channel();
    let output = Output::default();
    let sink = JsonEvidenceSink::new(
        SlowWriter {
            started: started_tx,
            resume: resume_rx,
            output: output.clone(),
            first: true,
        },
        NonZeroUsize::new(4).unwrap(),
    )
    .unwrap();
    sink.record_call(&record());
    started_rx.recv_timeout(Duration::from_secs(3)).unwrap();
    // The writer is held for this entire burst. Only four queued records may survive.
    for _ in 0..100_000 {
        sink.record_call(&record());
    }
    assert_eq!(sink.stats().dropped, 99_996);
    assert_eq!(sink.stats().written, 0);
    resume_tx.send(()).unwrap();
    until(|| sink.stats().written == 5);
    let text = String::from_utf8(output.0.lock().unwrap().clone()).unwrap();
    assert_eq!(text.lines().count(), 5);
    assert!(text
        .lines()
        .skip(1)
        .any(
            |line| serde_json::from_str::<serde_json::Value>(line).unwrap()["loss"]["dropped"]
                == 99_996
        ));
}

struct FailingWriter {
    failed: bool,
    output: Output,
}
impl Write for FailingWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if !self.failed {
            self.failed = true;
            return Err(io::Error::other("SYNTHETIC_SECRET_IO_ERROR"));
        }
        self.output.write(bytes)
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn writer_errors_are_isolated_counted_and_never_serialized() {
    let output = Output::default();
    let sink = JsonEvidenceSink::new(
        FailingWriter {
            failed: false,
            output: output.clone(),
        },
        NonZeroUsize::new(2).unwrap(),
    )
    .unwrap();
    sink.record_call(&record());
    until(|| sink.stats().write_failed == 1);
    sink.record_call(&record());
    until(|| sink.stats().written == 1);
    let text = String::from_utf8(output.0.lock().unwrap().clone()).unwrap();
    assert!(!text.contains("SYNTHETIC_SECRET"));
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&text).unwrap()["loss"]["write_failed"],
        1
    );
}
