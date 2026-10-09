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

//! Windows helper containment: create suspended, assign to an owned job, then resume.
//! Dropping the job terminates only this invocation and its descendants.
#![cfg(windows)]
use crate::{diagnostics::OUTPUT_LIMIT, error::SafeError};
use std::{
    fs::File,
    io::Read,
    os::windows::{ffi::OsStrExt, io::FromRawHandle},
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::*,
    Security::SECURITY_ATTRIBUTES,
    System::{JobObjects::*, Pipes::CreatePipe, Threading::*},
};
struct OwnedHandle(HANDLE);
impl Drop for OwnedHandle {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
fn wide(value: &std::ffi::OsStr) -> Vec<u16> {
    value.encode_wide().chain(Some(0)).collect()
}
fn pipe() -> Result<(File, OwnedHandle), SafeError> {
    let mut read = std::ptr::null_mut();
    let mut write = std::ptr::null_mut();
    let security = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: std::ptr::null_mut(),
        bInheritHandle: 1,
    };
    unsafe {
        if CreatePipe(&mut read, &mut write, &security, 0) == 0 {
            return Err(SafeError::Storage);
        }
        let reader = File::from_raw_handle(read);
        let writer = OwnedHandle(write);
        if SetHandleInformation(read, HANDLE_FLAG_INHERIT, 0) == 0 {
            return Err(SafeError::Storage);
        }
        Ok((reader, writer))
    }
}
fn collect(
    mut file: File,
    total: Arc<AtomicUsize>,
    exceeded: Arc<AtomicBool>,
) -> std::thread::JoinHandle<Result<Vec<u8>, SafeError>> {
    std::thread::spawn(move || {
        let mut output = Vec::new();
        let mut bytes = [0; 4096];
        loop {
            let size = file
                .read(&mut bytes)
                .map_err(|_| SafeError::InvalidResponse)?;
            if size == 0 {
                return Ok(output);
            }
            if total.fetch_add(size, Ordering::Relaxed) + size > OUTPUT_LIMIT {
                exceeded.store(true, Ordering::Relaxed);
                return Err(SafeError::ResponseTooLarge);
            }
            output.extend_from_slice(&bytes[..size]);
        }
    })
}
pub struct ProcessOutput {
    pub stdout: Vec<u8>,
    pub exit_code: i32,
}
/// Call only after native executable provenance checks. Arguments must come from the fixed command enum.
pub(crate) fn run(
    path: &Path,
    args: &[&str],
    deadline: Duration,
    cancelled: Arc<AtomicBool>,
) -> Result<ProcessOutput, SafeError> {
    if !path.is_absolute()
        || path.as_os_str().to_string_lossy().contains('"')
        || args
            .iter()
            .any(|arg| !arg.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-'))
    {
        return Err(SafeError::InvalidInput);
    }
    let application = wide(path.as_os_str());
    let mut command = wide(std::ffi::OsStr::new(&format!(
        "\"{}\" {}",
        path.display(),
        args.join(" ")
    )));
    let directory = wide(std::env::temp_dir().as_os_str());
    let (stdout, out_write) = pipe()?;
    let (stderr, err_write) = pipe()?;
    unsafe {
        let job = OwnedHandle(CreateJobObjectW(std::ptr::null(), std::ptr::null()));
        if job.0.is_null() {
            return Err(SafeError::Storage);
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            job.0,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const _,
            std::mem::size_of_val(&limits) as u32,
        ) == 0
        {
            return Err(SafeError::Storage);
        }
        let mut startup: STARTUPINFOW = std::mem::zeroed();
        startup.cb = std::mem::size_of_val(&startup) as u32;
        startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdOutput = out_write.0;
        startup.hStdError = err_write.0;
        let mut info: PROCESS_INFORMATION = std::mem::zeroed();
        if CreateProcessW(
            application.as_ptr(),
            command.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1,
            CREATE_SUSPENDED | CREATE_NO_WINDOW,
            std::ptr::null(),
            directory.as_ptr(),
            &startup,
            &mut info,
        ) == 0
        {
            return Err(SafeError::NotFound);
        }
        let process = OwnedHandle(info.hProcess);
        let thread = OwnedHandle(info.hThread);
        if AssignProcessToJobObject(job.0, process.0) == 0 {
            TerminateProcess(process.0, 1);
            WaitForSingleObject(process.0, 5000);
            return Err(SafeError::Storage);
        }
        drop(out_write);
        drop(err_write);
        let total = Arc::new(AtomicUsize::new(0));
        let exceeded = Arc::new(AtomicBool::new(false));
        let out = collect(stdout, total.clone(), exceeded.clone());
        let err = collect(stderr, total, exceeded.clone());
        let started = Instant::now();
        let result = if ResumeThread(thread.0) == u32::MAX {
            Err(SafeError::Storage)
        } else {
            loop {
                if cancelled.load(Ordering::Relaxed) {
                    break Err(SafeError::StaleContext);
                }
                if exceeded.load(Ordering::Relaxed) {
                    break Err(SafeError::ResponseTooLarge);
                }
                if started.elapsed() >= deadline {
                    break Err(SafeError::Timeout);
                }
                match WaitForSingleObject(process.0, 20) {
                    WAIT_OBJECT_0 => {
                        let mut code = 0;
                        if GetExitCodeProcess(process.0, &mut code) == 0 {
                            break Err(SafeError::Storage);
                        }
                        break Ok(code as i32);
                    }
                    WAIT_TIMEOUT => {}
                    _ => break Err(SafeError::Storage),
                }
            }
        };
        drop(job);
        WaitForSingleObject(process.0, 5000);
        let stdout = out.join().map_err(|_| SafeError::Storage)?;
        let stderr = err.join().map_err(|_| SafeError::Storage)?;
        let exit_code = result?;
        let stdout = stdout?;
        stderr?;
        Ok(ProcessOutput { stdout, exit_code })
    }
}
