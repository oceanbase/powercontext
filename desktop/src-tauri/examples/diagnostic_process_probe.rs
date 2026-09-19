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

//! Explicit native subprocess-budget probe. Never registered as a product IPC command.
#[cfg(windows)]
use powercontext_desktop::{diagnostics, error};
#[cfg(windows)]
#[path = "../src/diagnostic_process.rs"]
mod diagnostic_process;
#[cfg(windows)]
fn main() {
    use std::os::windows::process::CommandExt;
    use std::{
        io::Write,
        sync::{
            Arc,
            atomic::{AtomicBool, Ordering},
        },
        time::Duration,
    };
    match std::env::args().nth(1).as_deref() {
        Some("exitone") => {
            println!("{{\"status\":\"failed\"}}");
            std::process::exit(1);
        }
        Some("tree") => {
            // Intentionally outlive this root to verify the native Job cleans descendants.
            #[allow(clippy::zombie_processes)]
            let child = std::process::Command::new(std::env::current_exe().unwrap())
                .arg("wait")
                .creation_flags(0x08000000)
                .spawn()
                .unwrap();
            println!("{}", child.id());
            return;
        }
        Some("wait") => {
            std::thread::sleep(Duration::from_secs(30));
            return;
        }
        Some("overflow") => {
            let output = vec![b'x'; diagnostics::OUTPUT_LIMIT + 1];
            let _ = std::io::stdout().write_all(&output);
            std::thread::sleep(Duration::from_secs(30));
            return;
        }
        _ => {}
    }
    let executable = std::env::current_exe().unwrap();
    struct Sentinel(std::process::Child);
    impl Drop for Sentinel {
        fn drop(&mut self) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
    let mut sentinel = Sentinel(
        std::process::Command::new(&executable)
            .arg("wait")
            .creation_flags(0x08000000)
            .spawn()
            .unwrap(),
    );
    let invoke =
        |arg, timeout, cancel| diagnostic_process::run(&executable, &[arg], timeout, cancel);
    let result = invoke(
        "exitone",
        Duration::from_secs(5),
        Arc::new(AtomicBool::new(false)),
    )
    .unwrap();
    assert_eq!(result.exit_code, 1);
    assert!(String::from_utf8(result.stdout).unwrap().contains("failed"));
    assert!(matches!(
        invoke(
            "wait",
            Duration::from_millis(200),
            Arc::new(AtomicBool::new(false))
        ),
        Err(error::SafeError::Timeout)
    ));
    assert!(matches!(
        invoke(
            "overflow",
            Duration::from_secs(5),
            Arc::new(AtomicBool::new(false))
        ),
        Err(error::SafeError::ResponseTooLarge)
    ));
    let cancelled = Arc::new(AtomicBool::new(false));
    let flag = cancelled.clone();
    let trigger = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(200));
        flag.store(true, Ordering::Relaxed);
    });
    assert!(matches!(
        invoke("wait", Duration::from_secs(5), cancelled),
        Err(error::SafeError::StaleContext)
    ));
    trigger.join().unwrap();
    let started = std::time::Instant::now();
    let tree = invoke(
        "tree",
        Duration::from_secs(5),
        Arc::new(AtomicBool::new(false)),
    )
    .unwrap();
    let child_pid: u32 = std::str::from_utf8(&tree.stdout)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert!(started.elapsed() < Duration::from_secs(5));
    unsafe {
        use windows_sys::Win32::{Foundation::*, System::Threading::*};
        let child = OpenProcess(PROCESS_SYNCHRONIZE, 0, child_pid);
        if child.is_null() {
            assert_eq!(GetLastError(), ERROR_INVALID_PARAMETER);
        } else {
            let status = WaitForSingleObject(child, 1000);
            CloseHandle(child);
            assert_eq!(status, WAIT_OBJECT_0);
        }
    }
    assert!(
        sentinel.0.try_wait().unwrap().is_none(),
        "unrelated process must survive"
    );
    println!(
        "PASS: valid nonzero output, bounded output, timeout, cancellation, descendant cleanup, unrelated process preserved"
    );
}
#[cfg(not(windows))]
fn main() {
    panic!("Windows qualification required");
}
