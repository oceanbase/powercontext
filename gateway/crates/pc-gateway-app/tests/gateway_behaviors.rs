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

//! Public gateway behavior through the actual binary and loopback HTTP peers.

use axum::{body::Bytes, extract::State, http::HeaderMap, routing::post, Json, Router};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, VecDeque},
    path::Path,
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    time::Duration,
};

struct Reply {
    body: Vec<u8>,
    content_type: &'static str,
    status: u16,
}

impl Reply {
    fn fixture(case: &str, index: usize) -> Self {
        let streaming = case.ends_with("_streaming");
        let extension = if streaming { "sse" } else { "json" };
        Self {
            body: std::fs::read(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests/fixtures/republic/cases")
                    .join(case)
                    .join(format!("{index}.{extension}")),
            )
            .unwrap(),
            content_type: if streaming {
                "text/event-stream"
            } else {
                "application/json"
            },
            status: 200,
        }
    }
}

#[derive(Default)]
struct Peers {
    replies: Mutex<VecDeque<Reply>>,
    sent: Mutex<Vec<(HeaderMap, Bytes)>>,
    checks: Mutex<Vec<Value>>,
    deny: Option<&'static str>,
}

async fn upstream(
    State(peers): State<Arc<Peers>>,
    headers: HeaderMap,
    body: Bytes,
) -> axum::response::Response {
    use axum::response::IntoResponse;
    peers.sent.lock().unwrap().push((headers, body));
    let reply = peers
        .replies
        .lock()
        .unwrap()
        .pop_front()
        .expect("unexpected upstream send");
    (
        axum::http::StatusCode::from_u16(reply.status).unwrap(),
        [("content-type", reply.content_type)],
        reply.body,
    )
        .into_response()
}

async fn check(State(peers): State<Arc<Peers>>, Json(request): Json<Value>) -> Json<Value> {
    let phase = request["phase"].as_str().unwrap().to_owned();
    peers.checks.lock().unwrap().push(request);
    Json(json!({
        "schema": "powercontext.context-check.v1", "phase": phase,
        "decision": if peers.deny == Some(phase.as_str()) { "block" } else { "allow" },
        "reason_codes": [], "coverage": {"profile": "text-and-tools.v1", "complete": true},
        "policy_version": "test", "valid_until": "2999-01-01T00:00:00Z",
        "binding": format!("test-{phase}")
    }))
}

struct Gateway {
    child: Child,
    server: tokio::task::JoinHandle<()>,
    peers: Arc<Peers>,
    client: reqwest::Client,
    base: String,
}

impl Drop for Gateway {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        self.server.abort();
    }
}

impl Gateway {
    async fn start(replies: Vec<Reply>, deny: Option<&'static str>) -> Self {
        let peers = Arc::new(Peers {
            replies: Mutex::new(replies.into()),
            deny,
            ..Default::default()
        });
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let peer_base = format!("http://{}", listener.local_addr().unwrap());
        let router = Router::new()
            .route("/v1/context/check", post(check))
            .route(
                "/v1/capabilities",
                axum::routing::get(|| async { Json(json!({})) }),
            )
            .route("/v1/chat/completions", post(upstream))
            .route("/v1/responses", post(upstream))
            .with_state(peers.clone());
        let server = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        let reserved = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = reserved.local_addr().unwrap();
        drop(reserved);
        let child = Command::new(env!("CARGO_BIN_EXE_pc-gateway"))
            .env_clear()
            .env("PC_GATEWAY_LISTEN", address.to_string())
            .env("PC_GATEWAY_CORE_BASE", &peer_base)
            .env(
                "PC_GATEWAY_UPSTREAM_URL_CHAT",
                format!("{peer_base}/v1/chat/completions"),
            )
            .env(
                "PC_GATEWAY_UPSTREAM_URL_RESPONSES",
                format!("{peer_base}/v1/responses"),
            )
            .env("PC_GATEWAY_UPSTREAM_API_KEY", "provider-test-key")
            .env("PC_GATEWAY_API_KEY", "gateway-test-key")
            .env("PC_GATEWAY_INJECTION", "off")
            .env("PC_GATEWAY_SEDIMENT", "off")
            .stdout(Stdio::null())
            .stderr(Stdio::inherit())
            .spawn()
            .unwrap();
        let mut gateway = Self {
            child,
            server,
            peers,
            client: reqwest::Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(10))
                .build()
                .unwrap(),
            base: format!("http://{address}/gateway/v1"),
        };
        tokio::time::timeout(Duration::from_secs(10), async {
            loop {
                assert!(
                    gateway.child.try_wait().unwrap().is_none(),
                    "gateway exited during startup"
                );
                if let Ok(response) = gateway
                    .client
                    .get(format!("http://{address}/healthz"))
                    .send()
                    .await
                {
                    if response.status().is_success() {
                        break;
                    }
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .expect("gateway startup timed out");
        gateway
    }

    async fn post(&self, route: &str, request: &Value) -> reqwest::Response {
        self.client
            .post(format!("{}/{route}", self.base))
            .bearer_auth("gateway-test-key")
            .json(request)
            .send()
            .await
            .unwrap()
    }

    fn assert_forwarded(&self, requests: &[Value], replies: &[Vec<u8>]) {
        let sent = self.peers.sent.lock().unwrap();
        let checks = self.peers.checks.lock().unwrap();
        assert_eq!(sent.len(), requests.len());
        assert_eq!(checks.len(), requests.len() * 2);
        assert!(self.peers.replies.lock().unwrap().is_empty());
        for (index, (headers, body)) in sent.iter().enumerate() {
            assert_eq!(headers["authorization"], "Bearer provider-test-key");
            assert_eq!(
                serde_json::from_slice::<Value>(body).unwrap(),
                requests[index],
                "outgoing request {index}"
            );
            let send = &checks[index * 2];
            let release = &checks[index * 2 + 1];
            assert_eq!(send["phase"], "before_send");
            assert_eq!(
                send["payload"]["body"].as_str().unwrap().as_bytes(),
                body.as_ref()
            );
            assert_eq!(release["phase"], "before_release");
            assert_eq!(release["request_binding"], "test-before_send");
            assert_eq!(
                release["payload"]["body"].as_str().unwrap().as_bytes(),
                replies[index]
            );
        }
    }
}

fn tools(format: &str, scenario: &str) -> Vec<Value> {
    let definitions = match scenario {
        "simple" => return vec![],
        "tool" => vec![(
            "weather",
            "Get weather information for a location",
            json!({
                "type": "object", "properties": {"location": {"type": "string", "description": "the city"}},
                "required": ["location"]
            }),
        )],
        "multi_tool" => [
            ("add", "Add two numbers"),
            ("multiply", "Multiply two numbers"),
        ]
        .into_iter()
        .map(|(name, description)| {
            (
                name,
                description,
                json!({
                    "type": "object", "properties": {
                        "a": {"type": "integer", "description": "first number"},
                        "b": {"type": "integer", "description": "second number"}
                    }, "required": ["a", "b"]
                }),
            )
        })
        .collect(),
        _ => unreachable!(),
    };
    definitions.into_iter().map(|(name, description, parameters)| {
        if format == "chat" {
            json!({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
        } else {
            json!({"type": "function", "name": name, "description": description, "parameters": parameters, "strict": false})
        }
    }).collect()
}

fn request(format: &str, scenario: &str, streaming: bool) -> Value {
    let prompt = match scenario {
        "simple" => "Say 'Hello' in Portuguese.",
        "tool" => "What's the weather in Florence,Italy?",
        _ => "Add and multiply the number 2 and 3",
    };
    let mut body = json!({"model": "gpt-4o-mini", "stream": streaming});
    if format == "chat" {
        body["messages"] = json!([
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": prompt}
        ]);
        body["max_completion_tokens"] = json!(4000);
        if streaming {
            body["stream_options"] = json!({"include_usage": true});
        }
    } else {
        body["input"] = json!([
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]}
        ]);
        body["max_output_tokens"] = json!(4000);
        body["store"] = json!(false);
    }
    if scenario != "simple" {
        body["tools"] = json!(tools(format, scenario));
        body["tool_choice"] = json!("auto");
    }
    body
}

fn sse_data(body: &[u8]) -> Vec<Value> {
    std::str::from_utf8(body)
        .unwrap()
        .lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .filter(|data| *data != "[DONE]")
        .map(|data| serde_json::from_str(data).unwrap())
        .collect()
}

fn chat_message(body: &[u8], streaming: bool) -> Value {
    if !streaming {
        return serde_json::from_slice::<Value>(body).unwrap()["choices"][0]["message"].clone();
    }
    let mut text = String::new();
    let mut calls = BTreeMap::new();
    for event in sse_data(body) {
        let delta = &event["choices"][0]["delta"];
        if let Some(content) = delta["content"].as_str() {
            text.push_str(content);
        }
        for call in delta["tool_calls"].as_array().into_iter().flatten() {
            let current = calls
                .entry(call["index"].as_u64().unwrap())
                .or_insert_with(|| {
                    json!({
                        "id": "", "type": "function", "function": {"name": "", "arguments": ""}
                    })
                });
            if let Some(id) = call["id"].as_str() {
                current["id"] = json!(id);
            }
            for field in ["name", "arguments"] {
                if let Some(part) = call["function"][field].as_str() {
                    current["function"][field] = json!(format!(
                        "{}{part}",
                        current["function"][field].as_str().unwrap()
                    ));
                }
            }
        }
    }
    let mut message = json!({"role": "assistant", "content": text});
    if !calls.is_empty() {
        message["content"] = Value::Null;
        message["tool_calls"] = json!(calls.into_values().collect::<Vec<_>>());
    }
    message
}

fn tool_output(name: &str) -> &'static str {
    match name {
        "weather" => "40 C",
        "add" => "5",
        "multiply" => "6",
        _ => unreachable!(),
    }
}

#[tokio::test]
async fn chat_preserves_request_fields_and_tool_continuations() {
    for streaming in [false, true] {
        for scenario in ["simple", "tool", "multi_tool"] {
            let case = format!(
                "fantasy_chat_{scenario}{}",
                if streaming { "_streaming" } else { "" }
            );
            let count = if scenario == "simple" { 1 } else { 2 };
            let replies: Vec<_> = (0..count).map(|i| Reply::fixture(&case, i)).collect();
            let bodies: Vec<_> = replies.iter().map(|reply| reply.body.clone()).collect();
            let gateway = Gateway::start(replies, None).await;
            let mut body = request("chat", scenario, streaming);
            body["temperature"] = json!(0.2);
            body["parallel_tool_calls"] = json!(false);
            body["messages"][1]["name"] = json!("alice");
            let mut expected = vec![];
            for response_body in &bodies {
                expected.push(body.clone());
                let response = gateway.post("chat/completions", &body).await;
                assert_eq!(response.status(), 200, "{case}");
                assert_eq!(
                    response.headers()["content-type"],
                    if streaming {
                        "text/event-stream"
                    } else {
                        "application/json"
                    }
                );
                assert_eq!(
                    response.bytes().await.unwrap().as_ref(),
                    response_body,
                    "{case}"
                );
                let message = chat_message(response_body, streaming);
                if let Some(calls) = message["tool_calls"].as_array() {
                    let messages = body["messages"].as_array_mut().unwrap();
                    messages.push(message.clone());
                    for call in calls {
                        messages.push(json!({"role": "tool", "tool_call_id": call["id"], "content": tool_output(call["function"]["name"].as_str().unwrap())}));
                    }
                }
            }
            gateway.assert_forwarded(&expected, &bodies);
        }
    }
}

#[tokio::test]
async fn chat_preserves_length_finish_and_empty_tool_arguments() {
    for case in ["goose_chat_length", "goose_chat_empty_arguments"] {
        let reply = Reply::fixture(case, 0);
        let bytes = reply.body.clone();
        let gateway = Gateway::start(vec![reply], None).await;
        let body = request("chat", "simple", false);
        let response = gateway.post("chat/completions", &body).await;
        assert_eq!(response.status(), 200);
        assert_eq!(response.bytes().await.unwrap().as_ref(), bytes);
        gateway.assert_forwarded(&[body], &[bytes]);
    }
}

async fn released_responses(response: reqwest::Response, bytes: &[u8], streaming: bool) -> Value {
    assert_eq!(response.status(), 200);
    if !streaming {
        assert_eq!(response.bytes().await.unwrap().as_ref(), bytes);
    } else {
        assert_eq!(response.headers()["content-type"], "text/event-stream");
        let events = sse_data(&response.bytes().await.unwrap());
        assert_eq!(events[0]["type"], "response.created");
        let terminal = events.last().unwrap();
        assert_eq!(terminal["type"], "response.completed");
        assert_eq!(
            terminal["response"],
            serde_json::from_slice::<Value>(bytes).unwrap()
        );
        let expected: Value = serde_json::from_slice(bytes).unwrap();
        let done: Vec<_> = events
            .iter()
            .filter(|e| e["type"] == "response.output_item.done")
            .map(|e| e["item"].clone())
            .collect();
        assert_eq!(done, *expected["output"].as_array().unwrap());
        let expected_text: String = expected["output"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|item| item["content"].as_array().into_iter().flatten())
            .filter_map(|part| part["text"].as_str().or(part["refusal"].as_str()))
            .collect();
        let actual_text: String = events
            .iter()
            .filter(|event| {
                matches!(
                    event["type"].as_str(),
                    Some("response.output_text.delta" | "response.refusal.delta")
                )
            })
            .map(|event| event["delta"].as_str().unwrap())
            .collect();
        assert_eq!(actual_text, expected_text);
        for item in expected["output"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|item| item["type"] == "function_call")
        {
            let arguments: String = events
                .iter()
                .filter(|event| {
                    event["type"] == "response.function_call_arguments.delta"
                        && event["item_id"] == item["id"]
                })
                .map(|event| event["delta"].as_str().unwrap())
                .collect();
            assert_eq!(
                arguments,
                item["arguments"].as_str().unwrap(),
                "stream clients must receive tool arguments as deltas"
            );
            let done = events
                .iter()
                .find(|event| {
                    event["type"] == "response.function_call_arguments.done"
                        && event["item_id"] == item["id"]
                })
                .expect("stream clients must receive finalized tool arguments");
            assert_eq!(done["arguments"], item["arguments"]);
            assert_eq!(
                done["name"], item["name"],
                "SDK clients must retain the function identity"
            );
        }
    }
    serde_json::from_slice(bytes).unwrap()
}

#[tokio::test]
async fn responses_preserves_tool_continuations_and_stream_arguments() {
    for streaming in [false, true] {
        for scenario in ["simple", "tool", "multi_tool"] {
            let case = format!("fantasy_responses_{scenario}");
            let count = if scenario == "simple" { 1 } else { 2 };
            let replies: Vec<_> = (0..count).map(|i| Reply::fixture(&case, i)).collect();
            let bodies: Vec<_> = replies.iter().map(|reply| reply.body.clone()).collect();
            let gateway = Gateway::start(replies, None).await;
            let mut body = request("responses", scenario, streaming);
            let mut expected = vec![];
            for bytes in &bodies {
                let mut sent = body.clone();
                sent["stream"] = json!(false);
                expected.push(sent);
                let response =
                    released_responses(gateway.post("responses", &body).await, bytes, streaming)
                        .await;
                let input = body["input"].as_array_mut().unwrap();
                for call in response["output"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter(|item| item["type"] == "function_call")
                {
                    input.push(call.clone());
                    input.push(json!({"type": "function_call_output", "call_id": call["call_id"], "output": tool_output(call["name"].as_str().unwrap())}));
                }
            }
            gateway.assert_forwarded(&expected, &bodies);
        }
    }
}

#[tokio::test]
async fn responses_preserves_refusals_and_call_identity() {
    for streaming in [false, true] {
        for case in ["goose_responses_refusal", "goose_responses_call_id"] {
            let reply = Reply::fixture(case, 0);
            let bytes = reply.body.clone();
            let gateway = Gateway::start(vec![reply], None).await;
            let body = request("responses", "simple", streaming);
            released_responses(gateway.post("responses", &body).await, &bytes, streaming).await;
            let mut expected = body;
            expected["stream"] = json!(false);
            gateway.assert_forwarded(&[expected], &[bytes]);
        }
    }
}

#[tokio::test]
async fn denied_send_or_release_never_returns_recorded_content() {
    for phase in ["before_send", "before_release"] {
        let gateway =
            Gateway::start(vec![Reply::fixture("fantasy_chat_simple", 0)], Some(phase)).await;
        let response = gateway
            .post("chat/completions", &request("chat", "simple", false))
            .await;
        assert_eq!(response.status(), 403);
        assert!(!response.text().await.unwrap().contains("Olá"));
        assert_eq!(
            gateway.peers.sent.lock().unwrap().len(),
            usize::from(phase == "before_release")
        );
    }
}

#[tokio::test]
async fn chat_developer_instructions_reach_the_upstream_unchanged() {
    let reply = Reply::fixture("fantasy_chat_simple", 0);
    let bytes = reply.body.clone();
    let gateway = Gateway::start(vec![reply], None).await;
    let mut body = request("chat", "simple", false);
    body["messages"][0] = json!({"role": "developer", "content": "Be concise"});
    let response = gateway.post("chat/completions", &body).await;
    assert_eq!(response.status(), 200);
    assert_eq!(response.bytes().await.unwrap().as_ref(), bytes);
    gateway.assert_forwarded(&[body], &[bytes]);
}

#[tokio::test]
async fn responses_text_input_is_admitted_as_a_user_message() {
    let reply = Reply::fixture("fantasy_responses_simple", 0);
    let bytes = reply.body.clone();
    let gateway = Gateway::start(vec![reply], None).await;
    let mut body = request("responses", "simple", false);
    body["input"] = json!("Hello");
    let response = gateway.post("responses", &body).await;
    assert_eq!(response.status(), 200);
    assert_eq!(response.bytes().await.unwrap().as_ref(), bytes);
    body["input"] = json!([{"role": "user", "content": "Hello"}]);
    gateway.assert_forwarded(&[body], &[bytes]);
}

#[tokio::test]
async fn upstream_errors_keep_their_status_and_body_for_streaming_clients() {
    for format in ["chat", "responses"] {
        for status in [400, 429] {
            let bytes =
                br#"{"error":{"type":"request_error","message":"provider refused"}}"#.to_vec();
            let reply = Reply {
                body: bytes.clone(),
                content_type: "application/json",
                status,
            };
            let gateway = Gateway::start(vec![reply], None).await;
            let mut body = request(format, "simple", true);
            let route = if format == "chat" {
                "chat/completions"
            } else {
                "responses"
            };
            let response = gateway.post(route, &body).await;
            assert_eq!(response.status().as_u16(), status);
            assert_eq!(response.headers()["content-type"], "application/json");
            assert_eq!(response.bytes().await.unwrap().as_ref(), bytes);
            if format == "responses" {
                body["stream"] = json!(false);
            }
            gateway.assert_forwarded(&[body], &[bytes]);
        }
    }
}

#[tokio::test]
async fn responses_stream_delivers_reasoning_summaries_and_citations() {
    let bytes = std::fs::read(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/responses_rich.json"),
    )
    .unwrap();
    let gateway = Gateway::start(
        vec![Reply {
            body: bytes.clone(),
            content_type: "application/json",
            status: 200,
        }],
        None,
    )
    .await;
    let mut body = request("responses", "simple", true);
    let response = gateway.post("responses", &body).await;
    assert_eq!(response.status(), 200);
    let events = sse_data(&response.bytes().await.unwrap());
    let summaries: Vec<_> = events
        .iter()
        .filter(|event| event["type"] == "response.reasoning_summary_text.delta")
        .map(|event| (event["summary_index"].clone(), event["delta"].clone()))
        .collect();
    assert_eq!(
        summaries,
        vec![
            (json!(0), json!("Check the evidence.")),
            (json!(1), json!("Use the result."))
        ]
    );
    let annotations: Vec<_> = events
        .iter()
        .filter(|event| event["type"] == "response.output_text.annotation.added")
        .collect();
    assert_eq!(annotations.len(), 1);
    assert_eq!(
        annotations[0]["annotation"]["url"],
        "https://example.com/evidence"
    );
    assert_eq!(annotations[0]["annotation_index"], 0);
    assert_eq!(annotations[0]["item_id"], "msg_1");
    assert_eq!(events.last().unwrap()["type"], "response.completed");
    body["stream"] = json!(false);
    gateway.assert_forwarded(&[body], &[bytes]);
}
