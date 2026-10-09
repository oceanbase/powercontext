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

use powercontext_desktop::{
    error::SafeError,
    transport::{Endpoint, Transport},
};
use rcgen::{
    BasicConstraints, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair,
    KeyUsagePurpose,
};
use rustls::pki_types::PrivatePkcs8KeyDer;
use std::sync::Arc;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};
use tokio_rustls::TlsAcceptor;

async fn https_fixture(hostname: &str) -> (Endpoint, String, tokio::task::JoinHandle<()>) {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut ca = CertificateParams::new(vec![]).unwrap();
    ca.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    ca.distinguished_name
        .push(rcgen::DnType::CommonName, "S1 fixture CA");
    ca.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    let ca_key = KeyPair::generate().unwrap();
    let ca_cert = ca.self_signed(&ca_key).unwrap();
    let issuer = Issuer::new(ca, ca_key);
    let mut leaf = CertificateParams::new(vec![hostname.into()]).unwrap();
    leaf.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    let leaf_key = KeyPair::generate().unwrap();
    let leaf_cert = leaf.signed_by(&leaf_key, &issuer).unwrap();
    let config = rustls::ServerConfig::builder()
        .with_no_client_auth()
        .with_single_cert(
            vec![leaf_cert.der().clone(), ca_cert.der().clone()],
            PrivatePkcs8KeyDer::from(leaf_key.serialize_der()).into(),
        )
        .unwrap();
    let acceptor = TlsAcceptor::from(Arc::new(config));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = Endpoint::parse(&format!(
        "https://127.0.0.1:{}",
        listener.local_addr().unwrap().port()
    ))
    .unwrap();
    let task = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        if let Ok(mut tls) = acceptor.accept(stream).await {
            let mut request = [0; 4096];
            if tls.read(&mut request).await.is_ok() {
                let body = r#"{"status":"ok"}"#;
                let _ = tls.write_all(format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).as_bytes()).await;
                let _ = tls.shutdown().await;
            }
        }
    });
    (endpoint, ca_cert.pem(), task)
}

#[tokio::test]
async fn explicit_ca_is_connection_local_and_hostname_is_still_verified() {
    let (endpoint, ca, task) = https_fixture("127.0.0.1").await;
    Transport::new(Some(ca.as_bytes()))
        .unwrap()
        .liveness(&endpoint, None)
        .await
        .unwrap();
    task.await.unwrap();

    let (endpoint, _, task) = https_fixture("127.0.0.1").await;
    assert_eq!(
        Transport::new(None)
            .unwrap()
            .liveness(&endpoint, None)
            .await,
        Err(SafeError::Tls)
    );
    task.await.unwrap();

    let (endpoint, ca, task) = https_fixture("wrong.example").await;
    assert_eq!(
        Transport::new(Some(ca.as_bytes()))
            .unwrap()
            .liveness(&endpoint, None)
            .await,
        Err(SafeError::Tls)
    );
    task.await.unwrap();
}
