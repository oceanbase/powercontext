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

//! Real HTTP transport fixtures; these are local providers, not hosted-model acceptance.
use pc_gateway_evidence::{UsageCompleteness, UsageEvidence, UsageMapping};
use pc_gateway_transport::{AdmittedSend, ReqwestTransport, SendOutcome, Transport};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

async fn response(
    body: &'static str,
    content_type: &'static str,
) -> pc_gateway_transport::RawResponse {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (mut socket, _) = listener.accept().await.unwrap();
        let mut buffer = [0; 4096];
        assert!(socket.read(&mut buffer).await.unwrap() > 0);
        tokio::time::sleep(Duration::from_millis(40)).await;
        socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len()).as_bytes()).await.unwrap();
        let split = body.len() / 2;
        socket.write_all(&body.as_bytes()[..split]).await.unwrap();
        tokio::time::sleep(Duration::from_millis(20)).await;
        socket.write_all(&body.as_bytes()[split..]).await.unwrap();
    });
    let transport = ReqwestTransport::new(Duration::from_secs(5));
    let outcome = transport
        .send(AdmittedSend {
            binding: "test".into(),
            payload: bytes::Bytes::from_static(b"{}"),
            target_url: format!("http://{addr}/v1/responses"),
            headers: vec![],
            stream: content_type
                .split(';')
                .next()
                .unwrap_or("")
                .trim()
                .eq_ignore_ascii_case("text/event-stream"),
        })
        .await;
    server.await.unwrap();
    let SendOutcome::Delivered(raw) = outcome else {
        panic!("fixture response not delivered")
    };
    raw
}

#[tokio::test]
async fn json_usage_and_first_body_byte_include_the_header_wait() {
    let raw = response(r#"{"usage":{"input_tokens":100,"output_tokens":9,"input_tokens_details":{"cached_tokens":70}},"output":[]}"#, "application/json").await;
    assert!(
        raw.response_first_byte_ms.unwrap() >= 40,
        "header wait belongs to send-to-first-byte latency"
    );
    assert!(raw.response_complete_ms.unwrap() >= raw.response_first_byte_ms.unwrap() + 15);
    let usage = UsageEvidence::openai(raw.usage.as_ref(), UsageMapping::OpenAiResponses);
    assert_eq!(usage.input.tokens, Some(100));
    assert_eq!(usage.output.tokens, Some(9));
    assert_eq!(usage.cache_read.tokens, Some(70));
    assert_eq!(usage.cache_write.completeness, UsageCompleteness::Unknown);
}

#[tokio::test]
async fn sse_usage_only_chunks_and_missing_usage_keep_their_distinct_meaning() {
    let raw = response("data: {\"choices\":[{\"delta\":{\"content\":\"SENSITIVE\"}}]}\r\n\r\ndata: {\"usage\":{\"prompt_tokens\":80,\n\ndata: \"completion_tokens\":0,\"prompt_tokens_details\":{\"cached_tokens\":20}}}\n\ndata: [DONE]\n\n", "text/event-stream").await;
    // The deliberately malformed multi-line event cannot establish usage.
    assert!(raw.usage.is_none());
    let raw = response("data: {\"choices\":[]}\r\n\r\ndata: {\"usage\":{\"prompt_tokens\":80,\r\ndata: \"completion_tokens\":0,\"prompt_tokens_details\":{\"cached_tokens\":20}}}\r\n\r\ndata: [DONE]\r\n\r\n", "text/event-stream").await;
    let usage = UsageEvidence::openai(raw.usage.as_ref(), UsageMapping::OpenAiChat);
    assert_eq!(usage.input.tokens, Some(80));
    assert_eq!(usage.output.tokens, Some(0));
    assert_eq!(usage.output.completeness, UsageCompleteness::Known);
    assert_eq!(usage.cache_read.tokens, Some(20));
    let raw = response(
        "data: {\"choices\":[{\"delta\":{\"content\":\"SENSITIVE\"}}]}\n\ndata: [DONE]\n\n",
        "text/event-stream",
    )
    .await;
    assert_eq!(
        UsageEvidence::openai(raw.usage.as_ref(), UsageMapping::OpenAiChat),
        UsageEvidence::default()
    );
    let raw = response("event: response.completed\ndata: {\"response\":{\"usage\":{\"input_tokens\":7,\"output_tokens\":2}}}\n\n", "text/event-stream").await;
    let usage = UsageEvidence::openai(raw.usage.as_ref(), UsageMapping::OpenAiResponses);
    assert_eq!(usage.input.tokens, Some(7));
    assert!(usage.cache_read.tokens.is_none());
}

#[tokio::test]
async fn sse_usage_matches_case_insensitive_media_types_without_prefix_aliases() {
    let body = "data: {\"usage\":{\"prompt_tokens\":80,\"completion_tokens\":7,\"prompt_tokens_details\":{\"cached_tokens\":20}}}\n\ndata: [DONE]\n\n";
    for content_type in ["text/event-stream", "Text/Event-Stream; charset=utf-8"] {
        let raw = response(body, content_type).await;
        assert_eq!(raw.body.as_ref(), body.as_bytes());
        assert_eq!(raw.content_type, content_type);
        let usage = UsageEvidence::openai(raw.usage.as_ref(), UsageMapping::OpenAiChat);
        assert_eq!(usage.input.tokens, Some(80), "{content_type}");
        assert_eq!(usage.output.tokens, Some(7));
        assert_eq!(usage.cache_read.tokens, Some(20));
    }
    let raw = response(body, "text/event-stream-invalid").await;
    assert!(raw.usage.is_none());
}

#[tokio::test]
async fn sse_leading_bom_preserves_first_event_usage_and_original_bytes() {
    for (body, mapping) in [
        ("\u{feff}data: {\"usage\":{\"prompt_tokens\":80,\"completion_tokens\":7,\"prompt_tokens_details\":{\"cached_tokens\":20}}}\r\n\r\ndata: [DONE]\r\n\r\n", UsageMapping::OpenAiChat),
        ("\u{feff}data: {\"response\":{\"usage\":{\"input_tokens\":80,\"output_tokens\":7,\"input_tokens_details\":{\"cached_tokens\":20}}}}\n\n", UsageMapping::OpenAiResponses),
    ] {
        for content_type in ["text/event-stream", "Text/Event-Stream; charset=utf-8"] {
            let raw = response(body, content_type).await;
            assert_eq!(raw.body.as_ref(), body.as_bytes());
            let usage = UsageEvidence::openai(raw.usage.as_ref(), mapping);
            assert_eq!(usage.input.tokens, Some(80));
            assert_eq!(usage.output.tokens, Some(7));
            assert_eq!(usage.cache_read.tokens, Some(20));
            assert_eq!(usage.cache_write.completeness, UsageCompleteness::Unknown);
        }
    }
}

#[tokio::test]
async fn body_read_timeouts_and_truncations_keep_distinct_reasons() {
    use pc_gateway_transport::SendIndeterminateReason;
    for stream in [false, true] {
        for (stall, partial) in [(true, false), (true, true), (false, true)] {
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let address = listener.local_addr().unwrap();
            let (finish_tx, finish_rx) = tokio::sync::oneshot::channel::<()>();
            let server = tokio::spawn(async move {
                let (mut socket, _) = listener.accept().await.unwrap();
                let mut request = Vec::new();
                let mut chunk = [0; 4096];
                // The fixture request has the two-byte body "{}". Read it before
                // closing to isolate response truncation from an unread-request reset.
                while !request.ends_with(b"\r\n\r\n{}") {
                    let n = socket.read(&mut chunk).await.unwrap();
                    assert!(n > 0);
                    request.extend_from_slice(&chunk[..n]);
                }
                let mime = if stream {
                    "text/event-stream"
                } else {
                    "application/json"
                };
                socket.write_all(format!("HTTP/1.1 201 Created\r\nContent-Type: {mime}\r\nContent-Length: 4096\r\nConnection: close\r\n\r\n").as_bytes()).await.unwrap();
                if partial {
                    socket
                        .write_all(b"SYNTHETIC_PARTIAL_RESPONSE_SECRET")
                        .await
                        .unwrap();
                }
                if stall {
                    // Keep the socket open until the transport's timeout has fired.
                    let _ = finish_rx.await;
                }
            });
            let transport = ReqwestTransport::new(Duration::from_millis(100));
            let outcome = transport
                .send(AdmittedSend {
                    binding: "fixture".into(),
                    payload: bytes::Bytes::from_static(b"{}"),
                    target_url: format!("http://{address}/provider"),
                    headers: vec![],
                    stream,
                })
                .await;
            let _ = finish_tx.send(());
            server.await.unwrap();
            let SendOutcome::IncompleteResponse {
                reason,
                status,
                response_first_byte_ms,
            } = outcome
            else {
                panic!("expected an incomplete response, got {outcome:?}");
            };
            assert_eq!(status, 201);
            assert_eq!(response_first_byte_ms.is_some(), partial);
            assert!(matches!(
                (stall, reason),
                (true, SendIndeterminateReason::TimeoutAwaitingResponse)
                    | (false, SendIndeterminateReason::LostAfterHandoff)
            ));
        }
    }
}
