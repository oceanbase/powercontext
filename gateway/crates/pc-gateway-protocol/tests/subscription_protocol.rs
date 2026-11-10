// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
// Grok cases adapted from Magpie's grok_test.go (MIT); see LICENSES/oauth.md.

use pc_gateway_protocol::{
    CanonicalRequest, GatewayRenderer, RenderError, SubscriptionProtocol, SubscriptionRenderer,
    WireRenderer,
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
