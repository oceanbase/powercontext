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

#[cfg(windows)]
#[tokio::main]
async fn main() {
    use powercontext_desktop::diagnostics::{DiagnosticKind, LocalDiagnostics};
    let path = std::env::args_os()
        .nth(1)
        .expect("isolated fixture registration");
    let diagnostics = LocalDiagnostics::new(path.into());
    let service = diagnostics
        .run(DiagnosticKind::Service)
        .await
        .expect("real service status projection");
    let integrations = diagnostics
        .run(DiagnosticKind::Integrations)
        .await
        .expect("real integrations projection");
    assert!(
        service
            .items
            .iter()
            .any(|v| v.field == "registration" && v.status == "not_installed")
    );
    assert_eq!(integrations.hosts.len(), 8);
    println!(
        "{}",
        serde_json::json!({"service":service,"integrations":integrations})
    );
}
#[cfg(not(windows))]
fn main() {
    panic!("Windows qualification required");
}
