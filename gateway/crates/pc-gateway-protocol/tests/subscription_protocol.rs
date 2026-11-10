// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
// Grok cases adapted from Magpie's grok_test.go (MIT); see LICENSES/oauth.md.

use pc_gateway_protocol::{
    CanonicalRequest, GatewayRenderer, SubscriptionProtocol, SubscriptionRenderer, WireRenderer,
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
fn grok_namespaces_keep_call_pairing_and_unsupported_tools_do_not_reach_the_wire() {
    let request = request(
        json!([
            {"type":"function", "name":"shell"},
            {"type":"custom", "name":"apply_patch"},
            {"type":"namespace","name":"collaboration","tools":[
                {"type":"function","name":"spawn","parameters":{"type":"object"}},
                {"type":"custom","name":"freeform"}
            ]},
            {"type":"namespace","name":"functions","tools":[{"type":"function","name":"view"}]},
            {"type":"web_search","external_web_access":false}
        ]),
        vec![
            json!({"type":"function_call","name":"spawn","namespace":"collaboration","call_id":"c1","arguments":"{}"}),
            json!({"type":"function_call_output","call_id":"c1","output":"accepted"}),
            json!({"type":"reasoning","content":null,"encrypted_content":"opaque"}),
        ],
        json!({"type":"function","name":"spawn","namespace":"collaboration"}),
    );
    let renderer = SubscriptionRenderer {
        inner: GatewayRenderer::Responses(Default::default()),
        subscription: Some(SubscriptionProtocol::Grok),
    };
    let body: Value =
        serde_json::from_slice(&renderer.render(&request, None).unwrap().bytes).unwrap();
    assert_eq!(
        body["tools"],
        json!([
            {"type":"function","name":"shell"},
            {"type":"function","name":"collaboration__spawn","parameters":{"type":"object"}},
            {"type":"function","name":"view"},
            {"type":"web_search"},
        ])
    );
    assert_eq!(
        body["tool_choice"],
        json!({"type":"function","name":"collaboration__spawn"})
    );
    assert_eq!(
        body["input"],
        json!([
            {"type":"function_call","name":"collaboration__spawn","call_id":"c1","arguments":"{}"},
            {"type":"function_call_output","call_id":"c1","output":"accepted"},
            {"type":"reasoning","encrypted_content":"opaque"},
        ])
    );
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
fn codex_request_settings_and_unsupported_output_budget_are_explicit() {
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
    request.output_limits.max_output_tokens = Some(100);
    assert!(renderer.render(&request, None).is_err());
}
