/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

//! Bounded diagnostic execution in a private process group.
use crate::{diagnostics::OUTPUT_LIMIT, error::SafeError};
use std::{
    fs::File,
    io::Read,
    os::{
        fd::{AsRawFd, FromRawFd, IntoRawFd},
        unix::process::CommandExt,
    },
    path::Path,
    process::{Child, Command, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc,
    },
    time::{Duration, Instant},
};

/// The collection threads poll in short slices so a cancellation signal is
/// observed while a foreign writer keeps the pipe alive without producing data.
const POLL_SLICE: Duration = Duration::from_millis(50);

pub(crate) struct Output {
    pub stdout: Vec<u8>,
    pub exit_code: i32,
}
struct Group(i32);
impl Drop for Group {
    fn drop(&mut self) {
        // The child creates its own group before exec. Never signal the app's group.
        unsafe {
            libc::kill(-self.0, libc::SIGKILL);
        }
    }
}
fn collect(
    reader: impl Read + IntoRawFd + Send + 'static,
    deadline: Instant,
    cancelled: Arc<AtomicBool>,
    total: Arc<AtomicUsize>,
    exceeded: Arc<AtomicBool>,
) -> mpsc::Receiver<Result<Vec<u8>, SafeError>> {
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        // Owning the descriptor lets the closure close the read end exactly once,
        // even when the caller stops listening before the writer outside the
        // killed process group releases the pipe.
        let mut file = unsafe { File::from_raw_fd(reader.into_raw_fd()) };
        let mut output = Vec::new();
        let mut buffer = [0; 8192];
        let result = loop {
            if cancelled.load(Ordering::Relaxed) {
                break Err(SafeError::StaleContext);
            }
            if Instant::now() >= deadline {
                break Err(SafeError::Timeout);
            }
            let slice = deadline
                .saturating_duration_since(Instant::now())
                .min(POLL_SLICE)
                .as_millis()
                .try_into()
                .unwrap_or(i32::MAX);
            let mut descriptor = libc::pollfd {
                fd: file.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            let ready = unsafe { libc::poll(&mut descriptor, 1, slice) };
            if ready < 0
                && std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted
            {
                break Err(SafeError::Storage);
            }
            if ready <= 0 {
                continue;
            }
            match file.read(&mut buffer) {
                Ok(0) => break Ok(output),
                Ok(n) => {
                    if total.fetch_add(n, Ordering::Relaxed).saturating_add(n) > OUTPUT_LIMIT {
                        exceeded.store(true, Ordering::Relaxed);
                        break Err(SafeError::ResponseTooLarge);
                    }
                    output.extend_from_slice(&buffer[..n]);
                }
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => break Err(SafeError::Storage),
            }
        };
        let _ = sender.send(result);
    });
    receiver
}

/// Preserve collection failure instead of accepting missing or partial output.
fn receive(
    receiver: mpsc::Receiver<Result<Vec<u8>, SafeError>>,
    deadline: Instant,
) -> Result<Vec<u8>, SafeError> {
    let remaining = deadline
        .checked_duration_since(Instant::now())
        .ok_or(SafeError::Timeout)?;
    receiver
        .recv_timeout(remaining)
        .map_err(|error| match error {
            mpsc::RecvTimeoutError::Timeout => SafeError::Timeout,
            mpsc::RecvTimeoutError::Disconnected => SafeError::Storage,
        })?
}
// Observe completion without surrendering the process identity used by Group.
fn has_exited(child: &Child) -> std::io::Result<bool> {
    let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
    let result = unsafe {
        libc::waitid(
            libc::P_PID,
            child.id(),
            &mut info,
            libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
        )
    };
    if result == 0 {
        return Ok(siginfo_pid(&info) != 0);
    }
    let error = std::io::Error::last_os_error();
    if error.kind() == std::io::ErrorKind::Interrupted {
        return Ok(false);
    }
    Err(error)
}

fn siginfo_pid(info: &libc::siginfo_t) -> i32 {
    #[cfg(target_os = "linux")]
    {
        unsafe { info.si_pid() }
    }
    #[cfg(target_os = "macos")]
    {
        info.si_pid
    }
}

pub(crate) fn run(
    executable: &Path,
    arguments: &[&str],
    timeout: Duration,
    cancelled: Arc<AtomicBool>,
) -> Result<Output, SafeError> {
    let mut command = Command::new(executable);
    command
        .args(arguments)
        .current_dir(std::env::temp_dir())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0);
    #[cfg(target_os = "linux")]
    let mut child = {
        let mut spawn_attempts = 0;
        loop {
            match command.spawn() {
                Ok(child) => break child,
                Err(error)
                    if error.raw_os_error() == Some(libc::ETXTBSY) && spawn_attempts < 20 =>
                {
                    spawn_attempts += 1;
                    std::thread::sleep(Duration::from_millis(5));
                }
                Err(_) => return Err(SafeError::NotFound),
            }
        }
    };
    #[cfg(not(target_os = "linux"))]
    let mut child = command.spawn().map_err(|_| SafeError::NotFound)?;
    let group = Group(child.id() as i32);
    let deadline = Instant::now() + timeout;
    let total = Arc::new(AtomicUsize::new(0));
    let exceeded = Arc::new(AtomicBool::new(false));
    let stdout = collect(
        child.stdout.take().unwrap(),
        deadline,
        cancelled.clone(),
        total.clone(),
        exceeded.clone(),
    );
    let stderr = collect(
        child.stderr.take().unwrap(),
        deadline,
        cancelled.clone(),
        total,
        exceeded.clone(),
    );
    let result = loop {
        if cancelled.load(Ordering::Relaxed) {
            break Err(SafeError::StaleContext);
        }
        if exceeded.load(Ordering::Relaxed) {
            break Err(SafeError::ResponseTooLarge);
        }
        if Instant::now() >= deadline {
            break Err(SafeError::Timeout);
        }
        match has_exited(&child) {
            Ok(true) => break Ok(()),
            Ok(false) => std::thread::sleep(Duration::from_millis(10)),
            Err(_) => break Err(SafeError::Storage),
        }
    };
    drop(group);
    let status = child.wait().map_err(|_| SafeError::Storage);
    // A writer may survive outside the killed group. Process failure must not
    // wait for its pipes; collection threads still honor cancellation/deadline.
    result?;
    let exit_code = status?.code().unwrap_or(-1);
    let output = receive(stdout, deadline);
    let errors = receive(stderr, deadline);
    if cancelled.load(Ordering::Relaxed) {
        return Err(SafeError::StaleContext);
    }
    if exceeded.load(Ordering::Relaxed) {
        return Err(SafeError::ResponseTooLarge);
    }
    errors?;
    Ok(Output {
        stdout: output?,
        exit_code,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn shell(
        script: &str,
        timeout: Duration,
        cancelled: Arc<AtomicBool>,
    ) -> Result<Output, SafeError> {
        run(Path::new("/bin/sh"), &["-c", script], timeout, cancelled)
    }
    #[test]
    fn exit_observation_preserves_process_identity_until_group_cleanup() {
        let mut child = Command::new("/bin/sh")
            .args(["-c", "exit 7"])
            .process_group(0)
            .spawn()
            .unwrap();
        let group = Group(child.id() as i32);
        let deadline = Instant::now() + Duration::from_secs(5);
        while !has_exited(&child).unwrap() {
            assert!(Instant::now() < deadline, "launcher did not exit");
            std::thread::sleep(Duration::from_millis(10));
        }
        // A waitable zombie still owns its PID. Observing exit must leave that
        // identity reserved until we have finished signalling the process group.
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        let observed = unsafe {
            libc::waitid(
                libc::P_PID,
                child.id(),
                &mut info,
                libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
            )
        };
        let error = std::io::Error::last_os_error();
        drop(group);
        let status = child.wait().unwrap();
        assert_eq!(
            observed, 0,
            "child identity was released before cleanup: {error}"
        );
        assert_eq!(siginfo_pid(&info), child.id() as i32);
        assert_eq!(status.code(), Some(7));
    }

    #[test]
    fn captures_output_and_nonzero_exit() {
        let result = shell(
            "printf synthetic; exit 1",
            Duration::from_secs(2),
            Arc::new(AtomicBool::new(false)),
        )
        .unwrap();
        assert_eq!(result.stdout, b"synthetic");
        assert_eq!(result.exit_code, 1);
    }
    #[test]
    fn surviving_pipe_writer_does_not_extend_shutdown() {
        let directory = tempfile::tempdir().unwrap();
        let pid_file = directory.path().join("escape.pid");
        let script = format!(
            "perl -e 'use POSIX; POSIX::setsid(); open(my $fh, q(>), $ARGV[0]) or die; print $fh $$; close($fh); sleep 300' '{}'",
            pid_file.display()
        );
        let cancelled = Arc::new(AtomicBool::new(false));
        let flag = cancelled.clone();
        let invocation = std::thread::spawn(move || shell(&script, Duration::from_secs(15), flag));
        // Startup on a busy runner is not part of the shutdown budget. Wait for
        // the escaped writer to own the pipes before triggering cancellation.
        let ready_deadline = Instant::now() + Duration::from_secs(10);
        let pid = loop {
            if let Ok(text) = std::fs::read_to_string(&pid_file)
                && let Ok(pid) = text.trim().parse::<i32>()
            {
                break pid;
            }
            assert!(
                Instant::now() < ready_deadline,
                "escaped writer did not become ready"
            );
            std::thread::sleep(Duration::from_millis(10));
        };
        // Always terminate the escaped process group, including when an assertion fails.
        let _escaped = Group(pid);
        let started = Instant::now();
        cancelled.store(true, Ordering::Relaxed);
        let result = invocation.join().unwrap();
        let elapsed = started.elapsed();
        assert_eq!(result.err(), Some(SafeError::StaleContext));
        assert!(
            elapsed < Duration::from_secs(2),
            "bounded shutdown blocked {elapsed:?} behind a writer that escaped the killed group"
        );
    }
    #[test]
    fn exited_launcher_with_open_pipe_preserves_timeout_and_cancellation() {
        for cancel in [false, true] {
            let directory = tempfile::tempdir().unwrap();
            let escaped_pid = directory.path().join("escaped.pid");
            let leader_pid = directory.path().join("leader.pid");
            let script = format!(
                "echo $$ > '{leader}'; perl -e 'use POSIX; POSIX::setsid(); open(my $fh, q(>), $ARGV[0]) or die; print $fh $$; close($fh); sleep 300' '{escaped}' & while [ ! -s '{escaped}' ]; do sleep 0.01; done; printf synthetic",
                leader = leader_pid.display(),
                escaped = escaped_pid.display()
            );
            let cancelled = Arc::new(AtomicBool::new(false));
            let flag = cancelled.clone();
            let invocation =
                std::thread::spawn(move || shell(&script, Duration::from_secs(3), flag));
            let ready_deadline = Instant::now() + Duration::from_secs(2);
            let pid = loop {
                if let Ok(text) = std::fs::read_to_string(&escaped_pid)
                    && let Ok(pid) = text.trim().parse::<i32>()
                {
                    break pid;
                }
                assert!(
                    Instant::now() < ready_deadline,
                    "escaped writer did not start"
                );
                std::thread::sleep(Duration::from_millis(10));
            };
            let _escaped = Group(pid);
            let leader = std::fs::read_to_string(&leader_pid).unwrap();
            // The launcher must be reaped before cancellation, so run() is
            // collecting pipes rather than supervising a live launcher.
            loop {
                let status = Command::new("/bin/ps")
                    .args(["-o", "stat=", "-p", leader.trim()])
                    .output()
                    .unwrap();
                if status.stdout.is_empty() {
                    break;
                }
                assert!(Instant::now() < ready_deadline, "launcher was not reaped");
                std::thread::sleep(Duration::from_millis(10));
            }
            let started = Instant::now();
            if cancel {
                cancelled.store(true, Ordering::Relaxed);
            }
            assert_eq!(
                invocation.join().unwrap().err(),
                Some(if cancel {
                    SafeError::StaleContext
                } else {
                    SafeError::Timeout
                })
            );
            if cancel {
                assert!(started.elapsed() < Duration::from_secs(1));
            }
        }
    }

    #[test]
    fn timeout_and_live_cancellation_terminate_spawned_descendants() {
        for cancel in [false, true] {
            let directory = tempfile::tempdir().unwrap();
            let child_pid_file = directory.path().join("child.pid");
            let grandchild_pid_file = directory.path().join("grandchild.pid");
            let script = format!(
                "sh -c 'sleep 30 & echo $! > \"{grandchild}\"; wait' & echo $! > '{child}'; while [ ! -s '{grandchild}' ]; do sleep 0.01; done; wait",
                child = child_pid_file.display(),
                grandchild = grandchild_pid_file.display(),
            );
            let flag = Arc::new(AtomicBool::new(false));
            let cancellation = if cancel {
                let flag = flag.clone();
                let child_pid_file = child_pid_file.clone();
                let grandchild_pid_file = grandchild_pid_file.clone();
                Some(std::thread::spawn(move || {
                    let deadline = Instant::now() + Duration::from_secs(2);
                    while (!child_pid_file.exists() || !grandchild_pid_file.exists())
                        && Instant::now() < deadline
                    {
                        std::thread::sleep(Duration::from_millis(10));
                    }
                    flag.store(true, Ordering::Relaxed);
                }))
            } else {
                None
            };
            let result = shell(
                &script,
                Duration::from_millis(if cancel { 3000 } else { 200 }),
                flag,
            );
            if let Some(thread) = cancellation {
                thread.join().unwrap();
            }
            assert_eq!(
                result.err(),
                Some(if cancel {
                    SafeError::StaleContext
                } else {
                    SafeError::Timeout
                })
            );
            let pids: Vec<i32> = [child_pid_file, grandchild_pid_file]
                .into_iter()
                .map(|path| {
                    std::fs::read_to_string(path)
                        .unwrap()
                        .trim()
                        .parse()
                        .unwrap()
                })
                .collect();
            let deadline = Instant::now() + Duration::from_secs(2);
            for pid in pids {
                loop {
                    let status = Command::new("/bin/ps")
                        .args(["-o", "stat=", "-p", &pid.to_string()])
                        .output()
                        .unwrap();
                    let state = String::from_utf8_lossy(&status.stdout);
                    if state.trim().is_empty() || state.trim().starts_with('Z') {
                        break;
                    }
                    assert!(
                        Instant::now() < deadline,
                        "diagnostic descendant remains running"
                    );
                    std::thread::sleep(Duration::from_millis(10));
                }
            }
        }
    }

    #[test]
    fn cleanup_does_not_kill_an_unrelated_process_group() {
        let mut unrelated = Command::new("/bin/sh")
            .args(["-c", "sleep 30"])
            .process_group(0)
            .spawn()
            .unwrap();
        let unrelated_pid = unrelated.id() as i32;
        std::thread::sleep(Duration::from_millis(50));

        let result = shell(
            "sleep 30",
            Duration::from_millis(100),
            Arc::new(AtomicBool::new(false)),
        );
        assert_eq!(result.err(), Some(SafeError::Timeout));
        assert_eq!(unsafe { libc::kill(unrelated_pid, 0) }, 0);

        drop(Group(unrelated_pid));
        unrelated.wait().unwrap();
    }

    #[test]
    fn bounds_output_deadline_and_cancellation() {
        assert!(matches!(
            shell(
                "yes synthetic",
                Duration::from_secs(2),
                Arc::new(AtomicBool::new(false))
            ),
            Err(SafeError::ResponseTooLarge)
        ));
        assert!(matches!(
            shell(
                "sleep 30 & wait",
                Duration::from_millis(100),
                Arc::new(AtomicBool::new(false))
            ),
            Err(SafeError::Timeout)
        ));
        assert!(matches!(
            shell(
                "sleep 30",
                Duration::from_secs(2),
                Arc::new(AtomicBool::new(true))
            ),
            Err(SafeError::StaleContext)
        ));
    }
}
