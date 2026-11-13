// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use pc_gateway_transport::{AdmittedSend, ReqwestTransport, SendOutcome, Transport};
use serde_json::json;
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
};

async fn send(body: String, content_type: Option<&str>) -> SendOutcome {
    let content_type = content_type
        .map(|value| format!("Content-Type: {value}\r\n"))
        .unwrap_or_default();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let peer = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut request = Vec::new();
        loop {
            let mut chunk = [0u8; 4096];
            let length = socket.read(&mut chunk).await.unwrap();
            assert!(length > 0);
            request.extend_from_slice(&chunk[..length]);
            if request.ends_with(b"{}") {
                break;
            }
        }
        socket
            .write_all(
                format!(
                    "HTTP/1.1 200 OK\r\n{content_type}Content-Length: {}\r\n\r\n",
                    body.len()
                )
                .as_bytes(),
            )
            .await
            .unwrap();
        for chunk in body.as_bytes().chunks(17) {
            socket.write_all(chunk).await.unwrap();
            tokio::task::yield_now().await;
        }
    });
    let result = ReqwestTransport::default()
        .with_subscription_responses_sse(true)
        .send(AdmittedSend {
            binding: "admission".into(),
            payload: bytes::Bytes::from_static(b"{}"),
            target_url: format!("http://{address}/responses"),
            headers: vec![],
            stream: true,
        })
        .await;
    peer.await.unwrap();
    result
}

#[tokio::test]
async fn buffered_terminal_response_contains_tools_reasoning_and_usage_for_release_check() {
    let response = json!({"id":"r1","object":"response","status":"completed","output":[
        {"type":"reasoning","encrypted_content":"opaque"},
        {"type":"function_call","call_id":"c1","name":"lookup","arguments":"{}"}
    ],"usage":{"input_tokens":10,"output_tokens":3}});
    let body=format!("event: response.created\r\ndata: {{\"type\":\"response.created\",\"response\":{{}}}}\r\n\r\nevent: response.completed\r\ndata: {}\r\n\r\ndata: [DONE]\r\n\r\n",
        json!({"type":"response.completed","response":response}));
    for content_type in [Some("text/event-stream"), None] {
        let SendOutcome::Delivered(raw) = send(body.clone(), content_type).await else {
            panic!("complete Response was lost");
        };
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&raw.body).unwrap(),
            response
        );
        assert_eq!(raw.content_type, "application/json");
        assert_eq!(
            raw.usage,
            Some(json!({"input_tokens":10,"output_tokens":3}))
        );
    }
}

#[tokio::test]
async fn missing_or_conflicting_terminal_response_is_indeterminate() {
    for body in [
        "data: {\"type\":\"response.output_text.delta\",\"delta\":\"partial\"}\n\n".into(),
        "data: {\"type\":\"response.completed\",\"response\":{\"output\":[]}}\n\ndata: {\"type\":\"error\",\"message\":\"late error\"}\n\n".into(),
    ] {
        assert!(matches!(send(body, Some("text/event-stream")).await,SendOutcome::IncompleteResponse { .. }));
    }
}

#[tokio::test]
async fn republic_codex_original_event_vectors_preserve_text_tools_and_reasoning() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/oauth/upstream/provider-vectors.json");
    let fixture: serde_json::Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    for key in ["codex", "codex_tool_roundtrip"] {
        let case = &fixture[key];
        let mut body = String::new();
        for event in case["events"].as_array().unwrap() {
            let data = if event == "[DONE]" {
                "[DONE]".into()
            } else {
                serde_json::to_string(event).unwrap()
            };
            body.push_str(&format!("data: {data}\n\n"));
        }
        let SendOutcome::Delivered(raw) = send(body, Some("text/event-stream")).await else {
            panic!("{key} was lost");
        };
        let response: serde_json::Value = serde_json::from_slice(&raw.body).unwrap();
        if let Some(expected) = case.get("expected_text") {
            assert_eq!(
                response["output"][0]["content"][0]["text"], *expected,
                "{}",
                case["source"]["test"]
            );
            assert_eq!(response["usage"], case["expected_usage"]);
        } else {
            assert_eq!(
                response["output"], case["expected_output"],
                "{}",
                case["source"]["test"]
            );
        }
    }
}
