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

use pc_gateway_transport::{AdmittedSend, ReqwestTransport, Transport};
use std::{process::Command, time::Duration};
use tokio::net::TcpListener;

#[test]
fn ambient_http_proxy_cannot_receive_an_approved_send() {
    // Environment variables are process-wide; run the probe in isolation so other
    // transport tests can execute concurrently without inheriting its proxy.
    let result = Command::new(std::env::current_exe().unwrap())
        .args(["--exact", "proxy_probe_child", "--nocapture"])
        .env("PC_GATEWAY_PROXY_PROBE", "1")
        .env_remove("HTTP_PROXY")
        .env_remove("http_proxy")
        .env_remove("HTTPS_PROXY")
        .env_remove("https_proxy")
        .env_remove("ALL_PROXY")
        .env_remove("all_proxy")
        .env_remove("NO_PROXY")
        .env_remove("no_proxy")
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}{}",
        String::from_utf8_lossy(&result.stdout),
        String::from_utf8_lossy(&result.stderr)
    );
}

#[test]
fn proxy_probe_child() {
    if std::env::var_os("PC_GATEWAY_PROXY_PROBE").is_none() {
        return;
    }
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let proxy = TcpListener::bind("127.0.0.1:0").await.unwrap();
        // The destination is deliberately non-resolvable. Without the proxy, no
        // network peer can receive the request; with reqwest's implicit system
        // proxy, this listener receives the full POST and provider credential.
        std::env::set_var(
            "HTTP_PROXY",
            format!("http://{}", proxy.local_addr().unwrap()),
        );
        let transport = ReqwestTransport::new(Duration::from_secs(2));
        let send = AdmittedSend {
            binding: "approved-binding".into(),
            payload: bytes::Bytes::from_static(br#"{"model":"approved","messages":[]}"#),
            target_url: "http://approved-model.invalid/v1/chat/completions".into(),
            headers: vec![("authorization".into(), "Bearer private-provider-key".into())],
            stream: false,
        };
        let sending = transport.send(send);
        tokio::pin!(sending);
        tokio::select! {
            biased;
            accepted = proxy.accept() => {
                let _ = accepted.unwrap();
                panic!("unapproved proxy received the approved model request");
            }
            _ = &mut sending => {}
            _ = tokio::time::sleep(Duration::from_secs(3)) => {
                panic!("model send did not finish within its transport deadline");
            }
        }
        assert!(
            tokio::time::timeout(Duration::from_millis(100), proxy.accept())
                .await
                .is_err(),
            "unapproved proxy received a model connection after the send completed"
        );
    });
}
