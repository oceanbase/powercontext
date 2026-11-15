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

//! Host-owned stderr health reporting, independent of the evidence writer and requests.
use pc_gateway_evidence::{EvidenceStats, JsonEvidenceSink};
use std::sync::{Arc, Weak};
use std::time::{Duration, Instant};

/// A single detached reporter per production sink. A blocked stderr holds only this
/// thread; it never blocks submissions, the stdout writer, or shutdown. No queue or
/// request data crosses this boundary, and dropping the sink retires the reporter.
pub(crate) fn start(sink: &Arc<JsonEvidenceSink>) -> std::io::Result<()> {
    let sink = Arc::downgrade(sink);
    let dispatch = tracing::dispatcher::get_default(Clone::clone);
    std::thread::Builder::new()
        .name("gateway-evidence-health".into())
        .spawn(move || {
            tracing::dispatcher::with_default(&dispatch, || report(sink));
        })
        .map(|_| ())
}

fn report(sink: Weak<JsonEvidenceSink>) {
    let mut write_failure_reported = false;
    let mut drop_reported = false;
    let mut last_summary = Instant::now();
    loop {
        std::thread::park_timeout(Duration::from_secs(1));
        // Release the sink before stderr I/O, so a blocked diagnostic cannot keep
        // its sender or stdout writer alive after the host drops the sink.
        let Some(stats) = sink.upgrade().map(|sink| sink.stats()) else {
            return;
        };
        let first_write_failure = stats.write_failed > 0 && !write_failure_reported;
        let first_drop = stats.dropped > 0 && !drop_reported;
        if first_write_failure {
            write_failure_reported = true;
            tracing::error!(
                event = "gateway.evidence.write_failed",
                "gateway evidence write failed; records unavailable"
            );
        }
        if first_drop {
            drop_reported = true;
            tracing::warn!(
                event = "gateway.evidence.dropped",
                "gateway evidence submissions dropped; coverage incomplete"
            );
        }
        if first_write_failure || first_drop || last_summary.elapsed() >= Duration::from_secs(30) {
            summary(stats);
            last_summary = Instant::now();
        }
    }
}

fn summary(stats: EvidenceStats) {
    // Fixed vocabulary and aggregate counts only, never writer errors or identities.
    if stats.dropped > 0 || stats.write_failed > 0 {
        tracing::warn!(
            event = "gateway.evidence.health",
            dropped = stats.dropped,
            write_failed = stats.write_failed,
            written = stats.written,
            "gateway evidence loss summary"
        );
    } else {
        tracing::info!(
            event = "gateway.evidence.health",
            dropped = stats.dropped,
            write_failed = stats.write_failed,
            written = stats.written,
            "gateway evidence health summary"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pc_gateway_evidence::{CallEvidence, EvidenceSink};
    use std::io::Write;
    use std::sync::{mpsc, Mutex};

    struct FailedOutput;
    impl Write for FailedOutput {
        fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
            Err(std::io::Error::other("SYNTHETIC_WRITER_SECRET"))
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    struct BlockedDiagnostics {
        entered: mpsc::Sender<()>,
        resume: Mutex<mpsc::Receiver<()>>,
    }
    impl Write for &BlockedDiagnostics {
        fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
            let _ = self.entered.send(());
            let _ = self.resume.lock().unwrap().recv();
            Ok(bytes.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for BlockedDiagnostics {
        type Writer = &'a BlockedDiagnostics;
        fn make_writer(&'a self) -> Self::Writer {
            self
        }
    }

    #[test]
    fn blocked_diagnostics_do_not_hold_the_sink_or_delay_submissions_and_drop() {
        let (entered_tx, entered_rx) = mpsc::channel();
        let (resume_tx, resume_rx) = mpsc::channel();
        let subscriber = tracing_subscriber::fmt()
            .with_ansi(false)
            .with_writer(BlockedDiagnostics {
                entered: entered_tx,
                resume: Mutex::new(resume_rx),
            })
            .finish();
        let sink = Arc::new(
            JsonEvidenceSink::new(FailedOutput, std::num::NonZeroUsize::new(1).unwrap()).unwrap(),
        );
        let weak = Arc::downgrade(&sink);
        tracing::subscriber::with_default(subscriber, || start(&sink).unwrap());
        let record = CallEvidence::default();
        sink.record_call(&record);
        entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        let (finished_tx, finished_rx) = mpsc::channel();
        std::thread::spawn(move || {
            for _ in 0..10_000 {
                sink.record_call(&record);
            }
            assert!(sink.stats().write_failed > 0);
            drop(sink);
            finished_tx.send(()).unwrap();
        });
        let finished = finished_rx.recv_timeout(Duration::from_secs(3));
        // Unblock even on failure; no test thread or writer should be stranded.
        drop(resume_tx);
        finished.unwrap();
        assert!(weak.upgrade().is_none());
    }
}
