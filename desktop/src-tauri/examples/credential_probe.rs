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

//! Opt-in native test. Stores only a synthetic marker under a unique test ID, then removes it.
use powercontext_desktop::credentials::{CredentialId, Secret, Vault, WindowsVault};
fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).is_some_and(|s| s == "read") {
        let id = CredentialId::new(args[2].clone()).unwrap();
        // Loading a valid secret after process restart proves it remained in the OS vault.
        WindowsVault
            .read(&id)
            .expect("native credential read failed");
        return;
    }
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let name = format!("s1-probe-{}-{nonce}", std::process::id());
    let id = CredentialId::new(name.clone()).unwrap();
    WindowsVault
        .put(
            &id,
            &Secret::new("synthetic-s1-credential-only".into()).unwrap(),
        )
        .expect("native credential write failed");
    let result = std::process::Command::new(std::env::current_exe().unwrap())
        .args(["read", &name])
        .status();
    let cleanup = WindowsVault.delete(&id);
    cleanup.expect("test credential cleanup failed");
    assert!(result.unwrap().success(), "child read failed");
    assert!(
        WindowsVault.read(&id).is_err(),
        "test credential was not removed"
    );
    println!("PASS: Windows vault write, new-process read, delete; no secret returned.");
}
