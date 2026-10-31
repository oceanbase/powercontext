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

//! A bounded, nonblocking JSONL pump. Never signals Ops or its service descendants.
use super::{CallGuard, OpsBridgeError as Error, OpsProgress, trust::Launch, wire};
use serde_json::Value;
use std::{
    io::{Read, Seek, Write},
    process::{Command, Stdio},
    sync::atomic::Ordering,
    time::{Duration, Instant},
};
const MAX_OUTPUT: usize = 1024 * 1024;
const MAX_LINE: usize = 64 * 1024;

pub(super) fn run(
    launch: Launch,
    request: Value,
    deadline: Duration,
    guard: CallGuard,
    emit: impl Fn(OpsProgress) + Send + 'static,
) -> Result<(Value, i32), Error> {
    let mut bytes = serde_json::to_vec(&request).map_err(|_| Error::InvalidInput)?;
    bytes.push(b'\n');
    if bytes.len() > MAX_LINE {
        return Err(Error::InvalidInput);
    }
    // An already-complete anonymous private file avoids a blocking stdin writer if
    // a broken helper never reads its request. The child still consumes one stdin line.
    let mut input = tempfile::tempfile_in(&launch.cwd).map_err(|_| Error::Storage)?;
    input
        .write_all(&bytes)
        .and_then(|_| input.rewind())
        .map_err(|_| Error::Storage)?;
    let mut command = Command::new(&launch.executable);
    command
        .args(&launch.args)
        .current_dir(&launch.cwd)
        .env_clear()
        .envs(&launch.env)
        .stdin(Stdio::from(input))
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|_| Error::ProcessFailure)?;
    let result = (|| {
        let mut stdout = child.stdout.take().ok_or(Error::ProcessFailure)?;
        let mut stderr = child.stderr.take().ok_or(Error::ProcessFailure)?;
        nonblocking(&stdout)?;
        nonblocking(&stderr)?;
        let mut stream = wire::Stream::new(&request)?;
        let mut line = Vec::new();
        let mut total = 0usize;
        let started = Instant::now();
        loop {
            if guard.active.closing.load(Ordering::Acquire) {
                return Err(Error::ShuttingDown);
            }
            if started.elapsed() >= deadline {
                return Err(Error::Timeout);
            }
            let mut buffer = [0u8; 8192];
            // Work per poll is bounded so a noisy stream cannot starve the deadline or stderr.
            for _ in 0..32 {
                let n = available(&mut stdout, &mut buffer)?;
                if n == 0 {
                    break;
                }
                total += n;
                if total > MAX_OUTPUT {
                    return Err(Error::OutputTooLarge);
                }
                for byte in &buffer[..n] {
                    if *byte == b'\n' {
                        let progress = stream.line(&line)?;
                        line.clear();
                        if let Some(progress) = progress {
                            emit(progress);
                        }
                    } else {
                        if line.len() >= MAX_LINE {
                            return Err(Error::OutputTooLarge);
                        }
                        line.push(*byte);
                    }
                }
            }
            for _ in 0..32 {
                let n = available(&mut stderr, &mut buffer)?;
                if n == 0 {
                    break;
                }
                total += n;
                if total > MAX_OUTPUT {
                    return Err(Error::OutputTooLarge);
                }
            }
            if let Some(status) = child.try_wait().map_err(|_| Error::ProcessFailure)? {
                // Parent exit is the boundary, not EOF: a descendant may retain either pipe.
                // Drain the final finite burst before deciding whether a complete result exists.
                loop {
                    let n = available(&mut stdout, &mut buffer)?;
                    if n == 0 {
                        break;
                    }
                    total += n;
                    if total > MAX_OUTPUT {
                        return Err(Error::OutputTooLarge);
                    }
                    for byte in &buffer[..n] {
                        if *byte == b'\n' {
                            let event = stream.line(&line)?;
                            line.clear();
                            if let Some(event) = event {
                                emit(event);
                            }
                        } else {
                            if line.len() >= MAX_LINE {
                                return Err(Error::OutputTooLarge);
                            }
                            line.push(*byte);
                        }
                    }
                }
                loop {
                    let n = available(&mut stderr, &mut buffer)?;
                    if n == 0 {
                        break;
                    }
                    total += n;
                    if total > MAX_OUTPUT {
                        return Err(Error::OutputTooLarge);
                    }
                }
                if !line.is_empty() {
                    return Err(Error::InvalidResponse);
                }
                return Ok((
                    stream.finish()?,
                    status.code().ok_or(Error::ProcessFailure)?,
                ));
            }
            std::thread::sleep(Duration::from_millis(5));
        }
    })();
    if child.try_wait().ok().flatten().is_none() {
        // At most six native admission guards can own a helper/reaper. Retain its
        // slot until exit, even after timeout. Closing pipes lets Ops record a
        // progress failure at its next safe boundary; no write is replayed here.
        std::thread::spawn(move || {
            let _ = child.wait();
            drop(guard);
        });
    }
    result
}
#[cfg(unix)]
fn nonblocking<T: std::os::fd::AsRawFd>(pipe: &T) -> Result<(), Error> {
    let fd = pipe.as_raw_fd();
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFL);
        if flags < 0 || libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
            return Err(Error::ProcessFailure);
        }
    }
    Ok(())
}
#[cfg(unix)]
fn available<T: Read>(pipe: &mut T, buffer: &mut [u8]) -> Result<usize, Error> {
    match pipe.read(buffer) {
        Ok(n) => Ok(n),
        Err(e)
            if matches!(
                e.kind(),
                std::io::ErrorKind::WouldBlock | std::io::ErrorKind::Interrupted
            ) =>
        {
            Ok(0)
        }
        Err(_) => Err(Error::ProcessFailure),
    }
}
#[cfg(windows)]
fn nonblocking<T>(_: &T) -> Result<(), Error> {
    Ok(())
}
#[cfg(windows)]
fn available<T: Read + std::os::windows::io::AsRawHandle>(
    pipe: &mut T,
    buffer: &mut [u8],
) -> Result<usize, Error> {
    use windows_sys::Win32::{
        Foundation::{ERROR_BROKEN_PIPE, GetLastError},
        System::Pipes::PeekNamedPipe,
    };
    let mut count = 0;
    unsafe {
        if PeekNamedPipe(
            pipe.as_raw_handle(),
            std::ptr::null_mut(),
            0,
            std::ptr::null_mut(),
            &mut count,
            std::ptr::null_mut(),
        ) == 0
        {
            return if GetLastError() == ERROR_BROKEN_PIPE {
                Ok(0)
            } else {
                Err(Error::ProcessFailure)
            };
        }
    }
    if count == 0 {
        return Ok(0);
    }
    let n = (count as usize).min(buffer.len());
    pipe.read(&mut buffer[..n])
        .map_err(|_| Error::ProcessFailure)
}
#[cfg(not(any(unix, windows)))]
fn nonblocking<T>(_: &T) -> Result<(), Error> {
    Err(Error::UnsupportedPlatform)
}
#[cfg(not(any(unix, windows)))]
fn available<T>(_: &mut T, _: &mut [u8]) -> Result<usize, Error> {
    Err(Error::UnsupportedPlatform)
}
