// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use pc_gateway_transport::{AdmittedSend, ReqwestTransport, SendOutcome, Transport};
use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

async fn exchange(request: &Value, response: &Value, limit: usize) -> SendOutcome {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let payload = serde_json::to_vec(request).unwrap();
    let expected = payload.clone();
    let response = serde_json::to_vec(response).unwrap();
    let peer = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut received = Vec::new();
        while !received.ends_with(&expected) {
            let mut chunk = [0; 4096];
            let length = socket.read(&mut chunk).await.unwrap();
            assert!(length > 0);
            received.extend_from_slice(&chunk[..length]);
        }
        assert_eq!(&received[received.len() - expected.len()..], expected);
        socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n", response.len()).as_bytes()).await.unwrap();
        socket.write_all(&response).await.unwrap();
    });
    let result = ReqwestTransport::default()
        .with_codeassist_chat(true)
        .with_buffer_limit(limit)
        .send(AdmittedSend {
            binding: "approved-final-codeassist-envelope".into(),
            payload: payload.into(),
            target_url: format!("http://{address}/v1internal:generateContent"),
            headers: vec![],
            stream: false,
        })
        .await;
    peer.await.unwrap();
    result
}

#[tokio::test]
async fn goose_envelopes_cross_real_http_and_only_complete_bounded_chat_is_released() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/oauth/upstream/provider-vectors.json");
    let fixtures: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let vector = &fixtures["gemini"];
    let request = &vector["expected_request"];
    let mut response = vector["response"].clone();
    assert!(matches!(
        exchange(request, &response, 4096).await,
        SendOutcome::IncompleteResponse { .. }
    ));
    // The original SSE chunk has no terminal reason. Only this field is adapted.
    response["response"]["candidates"][0]["finishReason"] = "STOP".into();
    let SendOutcome::Delivered(raw) = exchange(request, &response, 4096).await else {
        panic!("complete response lost")
    };
    let chat: Value = serde_json::from_slice(&raw.body).unwrap();
    assert_eq!(
        chat["choices"][0]["message"]["content"],
        vector["expected_response"]["candidates"][0]["content"]["parts"][0]["text"]
    );
    assert_eq!(
        chat["usage"]["prompt_tokens"],
        vector["expected_response"]["usageMetadata"]["promptTokenCount"]
    );
    assert!(chat["usage"].get("completion_tokens").is_none());
    assert_eq!(chat["model"], request["model"]);
    assert_eq!(raw.content_type, "application/json");
    let mut reasoning = response.clone();
    reasoning["response"]["candidates"][0]["content"]["parts"]
        .as_array_mut()
        .unwrap()
        .push(serde_json::json!({"text":"visible reasoning","thought":true}));
    reasoning["response"]["usageMetadata"]["candidatesTokenCount"] = 3.into();
    reasoning["response"]["usageMetadata"]["thoughtsTokenCount"] = 5.into();
    let SendOutcome::Delivered(raw) = exchange(request, &reasoning, 4096).await else {
        panic!("reasoning lost")
    };
    let chat: Value = serde_json::from_slice(&raw.body).unwrap();
    assert_eq!(
        chat["choices"][0]["message"]["reasoning_content"],
        "visible reasoning"
    );
    assert_eq!(chat["usage"]["completion_tokens"], 8);
    let mut sealed = response.clone();
    sealed["response"]["candidates"][0]["content"]["parts"][0]["thoughtSignature"] =
        "sealed".into();
    assert!(matches!(
        exchange(request, &sealed, 4096).await,
        SendOutcome::IncompleteResponse { .. }
    ));
    let limit = serde_json::to_vec(&response).unwrap().len();
    assert!(matches!(
        exchange(request, &response, limit).await,
        SendOutcome::IncompleteResponse { .. }
    ));
}
