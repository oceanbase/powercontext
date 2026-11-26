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
// Grok cases adapted from Magpie's grok_test.go (MIT); see LICENSES/oauth.md.

use pc_gateway_protocol::{
    codeassist_chat_response, CanonicalRequest, GatewayRenderer, RenderError, SubscriptionProtocol,
    SubscriptionRenderer, WireRenderer,
};
use serde_json::{json, Value};

fn request(tools: Value, input: Vec<Value>, choice: Value) -> CanonicalRequest {
    serde_json::from_value(json!({
        "model":"test-model", "messages":input.into_iter().map(|item| json!({
            "role":"tool", "content":[item]
        })).collect::<Vec<_>>(), "tools":tools, "tool_choice":choice,
        "output_limits":{"max_output_tokens":null}, "stream":false,
    }))
    .unwrap()
}

#[test]
fn grok_rejects_ambiguous_identities_before_rendering_an_upstream_request() {
    let renderer = SubscriptionRenderer {
        inner: GatewayRenderer::Responses(Default::default()),
        subscription: Some(SubscriptionProtocol::Grok),
    };
    for (tools, input, choice) in [
        (
            json!([
                {"type":"namespace","name":"a","tools":[{"type":"function","name":"b__c"}]},
                {"type":"namespace","name":"a__b","tools":[{"type":"function","name":"c"}]}
            ]),
            vec![],
            Value::Null,
        ),
        (
            json!([
                {"type":"function","name":"a__b__c"},
                {"type":"namespace","name":"a","tools":[{"type":"function","name":"b__c"}]}
            ]),
            vec![],
            Value::Null,
        ),
        (
            json!([{"type":"namespace","name":"a__b","tools":[{"type":"function","name":"c"}]}]),
            vec![
                json!({"type":"function_call","namespace":"a","name":"b__c","call_id":"c1","arguments":"{}"}),
            ],
            Value::Null,
        ),
        (
            json!([{"type":"function","name":"a__b__c"}]),
            vec![],
            json!({"type":"allowed_tools","mode":"required","tools":[
                {"type":"function","namespace":"a","name":"b__c"}
            ]}),
        ),
    ] {
        assert!(matches!(
            renderer.render(&request(tools, input, choice), None),
            Err(RenderError::UnsupportedField { field, .. }) if field == "ambiguous flattened tool name"
        ));
    }
}

#[test]
fn grok_lone_choice_and_plain_agent_message_follow_backend_shape() {
    let input = vec![
        json!({"type":"agent_message","author":"lead","recipient":"worker",
        "content":[{"type":"input_text","text":"Investigate"}]}),
        json!({"type":"agent_message","encrypted_content":"sealed", "content":[]}),
    ];
    let renderer = SubscriptionRenderer {
        inner: GatewayRenderer::Responses(Default::default()),
        subscription: Some(SubscriptionProtocol::Grok),
    };
    let body: Value = serde_json::from_slice(
        &renderer
            .render(&request(json!([]), input, json!("none")), None)
            .unwrap()
            .bytes,
    )
    .unwrap();
    assert!(body.get("tool_choice").is_none());
    assert_eq!(
        body["input"][0],
        json!({"type":"message","role":"user","content":[
            {"type":"input_text","text":"From lead to worker\n\n"}, {"type":"input_text","text":"Investigate"}
        ]})
    );
    assert_eq!(body["input"][1]["encrypted_content"], "sealed");
}

#[test]
fn codex_request_settings_are_explicit_and_an_output_limit_is_left_out() {
    let mut request = request(
        json!([]),
        vec![json!({"role":"user","content":"hello"})],
        Value::Null,
    );
    request.tool_choice = None;
    request.store = Some(true);
    let renderer = SubscriptionRenderer {
        inner: GatewayRenderer::Responses(Default::default()),
        subscription: Some(SubscriptionProtocol::Codex),
    };
    let body: Value =
        serde_json::from_slice(&renderer.render(&request, None).unwrap().bytes).unwrap();
    assert_eq!(body["stream"], true);
    assert_eq!(body["store"], false);
    assert_eq!(body["instructions"], "You are Codex.");
    // The endpoint rejects an output limit; agents other than the Codex CLI send one.
    request.output_limits.max_output_tokens = Some(100);
    let limited: Value =
        serde_json::from_slice(&renderer.render(&request, None).unwrap().bytes).unwrap();
    assert_eq!(limited, body);

    // The Chat route is not available on this subscription.
    let chat = SubscriptionRenderer {
        inner: GatewayRenderer::Chat(Default::default()),
        subscription: Some(SubscriptionProtocol::Codex),
    };
    assert!(chat.render(&request, None).is_err());
}

#[test]
fn gemini_parallel_tool_results_keep_call_identity_in_either_order() {
    let renderer = SubscriptionRenderer {
        inner: GatewayRenderer::Chat(Default::default()),
        subscription: Some(SubscriptionProtocol::Gemini {
            project: "project-123".into(),
        }),
    };
    let calls = json!([
        {"id":"call-paris","type":"function","function":{
            "name":"weather","arguments":"{\"city\":\"Paris\"}"
        },"extra_content":{"google":{"thought_signature":"signature-paris"}}},
        {"id":"call-tokyo","type":"function","function":{
            "name":"weather","arguments":"{\"city\":\"Tokyo\"}"
        },"extra_content":{"google":{"thought_signature":"signature-tokyo"}}}
    ]);
    for results in [
        [("call-paris", "rain"), ("call-tokyo", "sun")],
        [("call-tokyo", "sun"), ("call-paris", "rain")],
    ] {
        let request: CanonicalRequest = serde_json::from_value(json!({
            "model":"gemini-2.5-pro",
            "messages":[
                {"role":"assistant","content":[],"content_null":true,"tool_calls":calls},
                {"role":"tool","tool_call_id":results[0].0,"content":[results[0].1]},
                {"role":"tool","tool_call_id":results[1].0,"content":[results[1].1]}
            ],
            "output_limits":{"max_output_tokens":null}
        }))
        .unwrap();
        let body: Value =
            serde_json::from_slice(&renderer.render(&request, None).unwrap().bytes).unwrap();
        assert_eq!(
            body["request"]["contents"][0],
            json!({"role":"model","parts":[
                {"functionCall":{"id":"call-paris","name":"weather","args":{"city":"Paris"}},
                    "thoughtSignature":"signature-paris"},
                {"functionCall":{"id":"call-tokyo","name":"weather","args":{"city":"Tokyo"}},
                    "thoughtSignature":"signature-tokyo"}
            ]})
        );
        for (index, (id, result)) in results.iter().enumerate() {
            assert_eq!(
                body["request"]["contents"][index + 1],
                json!({"role":"user","parts":[{"functionResponse":{
                    "id":id,"name":"weather","response":{"output":result}
                }}]})
            );
        }
    }
}

#[test]
fn gemini_upstream_tool_ids_survive_a_chat_tool_continuation() {
    let parts = json!([
        {"functionCall":{"id":"upstream-paris","name":"weather","args":{"city":"Paris"}}},
        {"functionCall":{"id":"response-123-call-2","name":"weather","args":{"city":"Tokyo"}}},
        {"functionCall":{"name":"weather","args":{"city":"London"}}}
    ]);
    let response = json!({"response":{
        "responseId":"response-123",
        "candidates":[{"finishReason":"STOP","content":{"role":"model","parts":parts}}]
    }});
    let chat: Value = serde_json::from_slice(
        &codeassist_chat_response(&serde_json::to_vec(&response).unwrap(), "gemini-2.5-pro")
            .unwrap(),
    )
    .unwrap();
    let calls = &chat["choices"][0]["message"]["tool_calls"];
    assert_eq!(chat["choices"][0]["finish_reason"], "tool_calls");
    assert_eq!(calls[0]["id"], "upstream-paris");
    assert_eq!(calls[1]["id"], "response-123-call-2");
    let generated_id = calls[2]["id"].as_str().unwrap();
    assert!(!generated_id.is_empty());
    assert_ne!(calls[2]["id"], calls[0]["id"]);
    assert_ne!(calls[2]["id"], calls[1]["id"]);

    let renderer = SubscriptionRenderer {
        inner: GatewayRenderer::Chat(Default::default()),
        subscription: Some(SubscriptionProtocol::Gemini {
            project: "project-123".into(),
        }),
    };
    let mut messages = vec![json!({
        "role":"assistant","content":[chat["choices"][0]["message"]["content"]],
        "tool_calls":calls
    })];
    for index in [2, 1, 0] {
        messages.push(json!({"role":"tool","tool_call_id":calls[index]["id"],
            "content":[format!("{{\"result\":{index}}}")]}));
    }
    let request: CanonicalRequest = serde_json::from_value(json!({
        "model":"gemini-2.5-pro","messages":messages,
        "output_limits":{"max_output_tokens":null}
    }))
    .unwrap();
    let body: Value =
        serde_json::from_slice(&renderer.render(&request, None).unwrap().bytes).unwrap();
    let mut expected_parts = parts;
    expected_parts[2]["functionCall"]["id"] = json!(generated_id);
    assert_eq!(body["request"]["contents"][0]["parts"], expected_parts);
    for (message_index, call_index) in [2, 1, 0].into_iter().enumerate() {
        assert_eq!(
            body["request"]["contents"][message_index + 1]["parts"][0]["functionResponse"],
            json!({"id":calls[call_index]["id"],"name":"weather","response":{"result":call_index}})
        );
    }
}
