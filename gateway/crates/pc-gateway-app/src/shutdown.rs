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

//! Process shutdown deadlines and an independent last-resort exit watchdog.

use pc_gateway_core::ModelAdmission;
use std::{
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};

const DRAIN: Duration = Duration::from_secs(25);
const EXIT: Duration = Duration::from_secs(30);

#[derive(Clone)]
pub(crate) struct Deadlines {
    pub drain: tokio::time::Instant,
    pub exit: tokio::time::Instant,
    source: Option<Shutdown>,
}

impl Deadlines {
    pub(crate) fn from_signal(started: tokio::time::Instant) -> Self {
        Self {
            drain: started + DRAIN,
            exit: started + EXIT,
            source: None,
        }
    }

    // An older handler can publish after a later one. Never freeze a later
    // snapshot into connection timers or owner joins: they must shorten too.
    pub(crate) fn drain(&self) -> tokio::time::Instant {
        self.source
            .as_ref()
            .and_then(Shutdown::started)
            .map_or(self.drain, |start| {
                tokio::time::Instant::from_std(start) + DRAIN
            })
    }

    pub(crate) fn exit(&self) -> tokio::time::Instant {
        self.source
            .as_ref()
            .and_then(Shutdown::started)
            .map_or(self.exit, |start| {
                tokio::time::Instant::from_std(start) + EXIT
            })
    }

    async fn expired(&self, exit: bool) {
        loop {
            let deadline = if exit { self.exit() } else { self.drain() };
            let now = tokio::time::Instant::now();
            if now >= deadline {
                return;
            }
            // Polling avoids locks/notification in signal handlers and leaves
            // the independent process watchdog free of runtime-owned locks.
            let wake = if self.source.is_some() {
                deadline.min(now + Duration::from_millis(1))
            } else {
                deadline
            };
            tokio::time::sleep_until(wake).await;
        }
    }

    pub(crate) async fn drain_expired(&self) {
        self.expired(false).await;
    }
    pub(crate) async fn exit_expired(&self) {
        self.expired(true).await;
    }
}

struct Shared {
    origin: Instant,
    // Nanoseconds since origin. MAX means no signal. Written inside the signal
    // handler, before any runtime scheduling or potentially blocking operation.
    first_signal: AtomicU64,
    admission: ModelAdmission,
}

#[derive(Clone)]
pub(crate) struct Shutdown(Arc<Shared>);

impl Shutdown {
    fn unarmed(admission: ModelAdmission) -> Self {
        Self(Arc::new(Shared {
            origin: Instant::now(),
            first_signal: AtomicU64::new(u64::MAX),
            admission,
        }))
    }

    fn record_signal(&self) {
        // On our supported Unix platforms Instant uses monotonic clock_gettime
        // (async-signal-safe). No allocator, locks, logging, or Tokio in this path.
        self.record_signal_at(Instant::now());
    }

    fn record_signal_at(&self, timestamp: Instant) {
        let elapsed = timestamp
            .duration_since(self.0.origin)
            .as_nanos()
            .min(u128::from(u64::MAX - 1)) as u64;
        self.0.first_signal.fetch_min(elapsed, Ordering::SeqCst);
        self.0.admission.close();
    }

    fn started(&self) -> Option<Instant> {
        let nanos = self.0.first_signal.load(Ordering::SeqCst);
        (nanos != u64::MAX).then(|| self.0.origin + Duration::from_nanos(nanos))
    }

    pub(crate) fn deadlines(&self) -> Option<Deadlines> {
        self.started().map(|start| Deadlines {
            source: Some(self.clone()),
            ..Deadlines::from_signal(start.into())
        })
    }

    pub(crate) fn install(admission: ModelAdmission) -> std::io::Result<Self> {
        let shutdown = Self::unarmed(admission);
        for signal in [libc::SIGINT, libc::SIGTERM] {
            let handler = shutdown.clone();
            // SAFETY: record_signal only reads a monotonic clock and uses
            // lock-free atomics. Captured Arcs stay alive until process exit.
            unsafe {
                signal_hook_registry::register(signal, move || handler.record_signal())?;
            }
        }
        let watchdog = shutdown.clone();
        let (ready, started) = std::sync::mpsc::sync_channel(0);
        std::thread::Builder::new()
            .name("gateway-exit-watchdog".into())
            .spawn(move || {
                let _ = ready.send(());
                loop {
                    if watchdog
                        .started()
                        .is_some_and(|start| Instant::now() >= start + EXIT)
                    {
                        // No Rust drops, atexit hooks, task joins, runtime shutdown,
                        // or logging may delay this final abnormal exit.
                        unsafe {
                            libc::_exit(1);
                        }
                    }
                    std::thread::sleep(Duration::from_millis(1));
                }
            })?;
        started.recv().map_err(std::io::Error::other)?;
        // Deliberately detached and never disarmed, even after all request
        // owners end: runtime destruction still belongs to the exit budget.
        Ok(shutdown)
    }

    pub(crate) async fn signalled(&self) -> Deadlines {
        loop {
            if let Some(deadlines) = self.deadlines() {
                return deadlines;
            }
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repeated_signals_keep_the_first_absolute_deadlines() {
        let signal = Shutdown::unarmed(pc_gateway_core::ModelAdmission::default());
        signal.record_signal();
        let first = signal.deadlines().unwrap();
        std::thread::sleep(Duration::from_millis(5));
        signal.record_signal();
        let repeated = signal.deadlines().unwrap();
        assert_eq!(first.drain, repeated.drain);
        assert_eq!(first.exit, repeated.exit);
        assert_eq!(
            first.drain - tokio::time::Instant::from_std(signal.started().unwrap()),
            Duration::from_secs(25)
        );
        assert_eq!(
            first.exit - tokio::time::Instant::from_std(signal.started().unwrap()),
            Duration::from_secs(30)
        );
    }

    #[tokio::test(start_paused = true)]
    async fn delayed_first_handler_shortens_already_broadcast_deadlines() {
        let signal = Shutdown::unarmed(pc_gateway_core::ModelAdmission::default());
        let first = signal.0.origin;
        // The later handler publishes first, while the first handler was
        // preempted after reading its clock. Connections already received it.
        signal.record_signal_at(first + Duration::from_secs(5));
        let broadcast = signal.deadlines().unwrap();
        let drain = broadcast.clone();
        let exit = broadcast.clone();
        let draining = tokio::spawn(async move {
            drain.drain_expired().await;
            tokio::time::Instant::now()
        });
        let exiting = tokio::spawn(async move {
            exit.exit_expired().await;
            tokio::time::Instant::now()
        });
        tokio::time::advance(Duration::from_secs(20)).await;
        signal.record_signal_at(first);
        assert_eq!(
            broadcast.drain(),
            tokio::time::Instant::from_std(first) + Duration::from_secs(25)
        );
        assert_eq!(
            broadcast.exit(),
            tokio::time::Instant::from_std(first) + Duration::from_secs(30)
        );
        tokio::time::advance(Duration::from_secs(5)).await;
        // The absolute boundaries above are exact; Tokio's timer wheel rounds
        // wakes to milliseconds, including with deterministic paused time.
        let drained = draining.await.unwrap();
        let drain_limit = tokio::time::Instant::from_std(first) + Duration::from_secs(25);
        assert!((drain_limit..drain_limit + Duration::from_millis(1)).contains(&drained));
        assert!(!exiting.is_finished());
        tokio::time::advance(Duration::from_secs(5)).await;
        let exited = exiting.await.unwrap();
        let exit_limit = tokio::time::Instant::from_std(first) + Duration::from_secs(30);
        assert!((exit_limit..exit_limit + Duration::from_millis(1)).contains(&exited));
    }

    // Fault injection exists only in the test binary. Both children install the
    // product watchdog and drive the product listener, without a test timeout
    // being able to supply the successful exit evidence.
    #[test]
    fn stalled_process_child() {
        use std::io::Write;
        let Ok(kind) = std::env::var("PC_TEST_SHUTDOWN_CHILD") else {
            return;
        };
        let shutdown = Shutdown::install(pc_gateway_core::ModelAdmission::default()).unwrap();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        println!("LISTEN {}", listener.local_addr().unwrap());
        std::io::stdout().flush().unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        runtime.block_on(async move {
            let app = axum::Router::new().route(
                "/freeze",
                axum::routing::post(move || {
                    let kind = kind.clone();
                    async move {
                        let stall = || {
                            println!("STALLED");
                            std::io::stdout().flush().unwrap();
                            loop {
                                std::thread::park();
                            }
                        };
                        if kind == "request_runtime" {
                            stall();
                        }
                        tokio::task::spawn_blocking(stall);
                        "ok"
                    }
                }),
            );
            crate::connection::serve(
                tokio::net::TcpListener::from_std(listener).unwrap(),
                app,
                shutdown.signalled(),
            )
            .await
            .unwrap();
            println!("OWNERS_JOINED");
            std::io::stdout().flush().unwrap();
        });
        drop(runtime); // Running spawn_blocking work makes this wait forever.
        panic!("fault child incorrectly completed a normal runtime shutdown");
    }

    fn assert_default_process_exit(kind: &str) {
        use std::io::{BufRead, Write};
        use std::process::{Command, Stdio};
        struct Child(std::process::Child);
        impl Drop for Child {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        let mut child = Child(
            Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "shutdown::tests::stalled_process_child",
                    "--nocapture",
                ])
                .env("PC_TEST_SHUTDOWN_CHILD", kind)
                .stdout(Stdio::piped())
                .stderr(Stdio::inherit())
                .spawn()
                .unwrap(),
        );
        let stdout = child.0.stdout.take().unwrap();
        let (lines, output) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            for line in std::io::BufReader::new(stdout).lines() {
                if lines.send(line.unwrap()).is_err() {
                    break;
                }
            }
        });
        let address = loop {
            let line = output.recv_timeout(Duration::from_secs(5)).unwrap();
            if let Some(address) = line.strip_prefix("LISTEN ") {
                break address.to_owned();
            }
        };
        let mut client = std::net::TcpStream::connect(address).unwrap();
        client.write_all(b"POST /freeze HTTP/1.1\r\nHost: local\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
        loop {
            if output.recv_timeout(Duration::from_secs(5)).unwrap() == "STALLED" {
                break;
            }
        }
        // Measurement starts BEFORE the first signal and ends at actual exit.
        let started = Instant::now();
        assert_eq!(unsafe { libc::kill(child.0.id() as i32, libc::SIGINT) }, 0);
        if kind == "runtime_shutdown" {
            loop {
                if output.recv_timeout(Duration::from_secs(3)).unwrap() == "OWNERS_JOINED" {
                    break;
                }
            }
        }
        std::thread::sleep(Duration::from_secs(1));
        assert_eq!(unsafe { libc::kill(child.0.id() as i32, libc::SIGTERM) }, 0);
        let status = loop {
            if let Some(status) = child.0.try_wait().unwrap() {
                break status;
            }
            assert!(
                started.elapsed() < Duration::from_secs(32),
                "product watchdog did not exit; fixture kill is cleanup only"
            );
            std::thread::sleep(Duration::from_millis(5));
        };
        let elapsed = started.elapsed();
        assert_eq!(status.code(), Some(1), "must report abnormal termination");
        assert!(
            elapsed >= Duration::from_millis(29_990),
            "watchdog fired before the default exit deadline"
        );
        assert!(
            elapsed <= Duration::from_secs(32),
            "30s default plus 2s scheduling/measurement tolerance"
        );
        eprintln!(
            "platform={} kind={kind} budget=25/30s elapsed={elapsed:?} exit={status}",
            std::env::consts::OS
        );
    }

    #[test]
    fn default_watchdog_exits_when_request_runtime_cannot_yield() {
        assert_default_process_exit("request_runtime");
    }

    #[test]
    fn default_watchdog_remains_armed_through_runtime_shutdown() {
        assert_default_process_exit("runtime_shutdown");
    }
}
