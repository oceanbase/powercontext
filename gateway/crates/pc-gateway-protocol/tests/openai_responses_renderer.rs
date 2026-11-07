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

use pc_gateway_protocol::{
    CanonicalMessage, CanonicalRequest, OpenAiResponsesRenderer, OutputLimits, Role, WireRenderer,
};

fn request() -> CanonicalRequest {
    CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![serde_json::json!("hi")],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        }],
        tools: vec![],
        instructions: Some("be terse".into()),
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: Some(16),
        },
        stream: false,
        response_format: None,
        tool_choice: None,
        store: None,
        previous_response_id: None,
        conversation: None,
        passthrough_fields: None,
        token_limit_field: None,
    }
}

#[test]
fn renders_responses_wire_format() {
    let p = OpenAiResponsesRenderer.render(&request(), None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(v["model"], "m");
    assert_eq!(v["instructions"], "be terse");
    assert_eq!(v["max_output_tokens"], 16);
    assert_eq!(v["stream"], false);
    assert_eq!(v["input"][0]["role"], "user");
    assert_eq!(
        v["input"][0]["content"], "hi",
        "单字符串 content 必须保持 string（review P2：不得变 [\"hi\"]）"
    );
    assert!(v.get("messages").is_none(), "不得混入 chat 字段");
    assert!(v.get("max_tokens").is_none());
}

#[test]
fn responses_items_pass_through_verbatim() {
    let fc = serde_json::json!({
        "type": "function_call", "call_id": "c1", "name": "search", "arguments": "{}"
    });
    let req = CanonicalRequest {
        model: "m".into(),
        messages: vec![
            CanonicalMessage {
                role: Role::User,
                content: vec![serde_json::json!("hi")],
                content_null: false,
                tool_calls: None,
                tool_call_id: None,
            },
            // 无 role 的顶层 item：原样封进 content，渲染时作为顶层 item 透出
            CanonicalMessage {
                role: Role::Tool,
                content: vec![fc.clone()],
                content_null: false,
                tool_calls: None,
                tool_call_id: None,
            },
        ],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: Some(16),
        },
        stream: false,
        response_format: None,
        tool_choice: None,
        store: None,
        previous_response_id: None,
        conversation: None,
        passthrough_fields: None,
        token_limit_field: None,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(v["input"].as_array().unwrap().len(), 2);
    assert_eq!(v["input"][1], fc, "Responses 专有 item 必须字节级透传");
}

#[test]
fn merges_only_approved_cache_plan_fields() {
    let plan = serde_json::json!({"prompt_cache_key": "scope-1"});
    let p = OpenAiResponsesRenderer
        .render(&request(), Some(&plan))
        .unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(v["prompt_cache_key"], "scope-1");
    assert_eq!(v["model"], "m", "基础字段不被计划覆盖");
}

#[test]
fn typed_content_parts_pass_through_as_array() {
    // 多部件（typed parts）content 原样透传为数组；与单字符串形态并存不冲突。
    let req = CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![
                serde_json::json!({"type": "input_text", "text": "看这张图"}),
                serde_json::json!({"type": "input_image", "image_url": "data:image/png;base64,xxx"}),
            ],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        }],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: Some(16),
        },
        stream: false,
        response_format: None,
        tool_choice: None,
        store: None,
        previous_response_id: None,
        conversation: None,
        passthrough_fields: None,
        token_limit_field: None,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    let content = v["input"][0]["content"].as_array().unwrap();
    assert_eq!(content.len(), 2);
    assert_eq!(content[0]["type"], "input_text");
    assert_eq!(content[1]["type"], "input_image");
}

#[test]
fn structured_output_via_text_field_reaches_wire() {
    // PR review：Responses 的结构化输出在 text: {format: ...}，不在 response_format。
    // 经 Gateway 后 text.format 必须原样到达上游载荷。
    let req = CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![serde_json::json!("给我一个 JSON")],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        }],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: Some(64),
        },
        stream: false,
        response_format: Some(serde_json::json!({
            "format": {
                "type": "json_schema",
                "name": "my_output",
                "schema": {"type": "object", "properties": {"result": {"type": "string"}}}
            }
        })),
        tool_choice: None,
        store: None,
        previous_response_id: None,
        conversation: None,
        passthrough_fields: None,
        token_limit_field: None,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    // 上游载荷必须有 "text"（不是 "response_format"——那是 Chat 字段）
    assert!(
        v.get("text").is_some(),
        "text 字段必须存在于 Responses wire body"
    );
    assert_eq!(v["text"]["format"]["type"], "json_schema");
    assert_eq!(v["text"]["format"]["name"], "my_output");
    assert!(
        v.get("response_format").is_none(),
        "response_format 不得出现在 Responses wire body"
    );
}

#[test]
fn store_false_reaches_responses_wire_body() {
    // P1 review：#2313 要求 openai-responses 目标显式 store:false。
    // 经网关后 store 必须原样到达上游载荷，否则 Core 返回 409。
    let req = CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![serde_json::json!("hi")],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        }],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: Some(64),
        },
        stream: false,
        response_format: None,
        tool_choice: None,
        store: Some(false),
        previous_response_id: None,
        conversation: None,
        passthrough_fields: None,
        token_limit_field: None,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(
        v["store"], false,
        "store:false 必须到达 Responses wire body"
    );
}

#[test]
fn store_absent_omitted_from_wire_body() {
    // 调用方没传 store → 线格式不出现该字段（不冒认默认值）。
    let req = CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![serde_json::json!("hi")],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        }],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: Some(64),
        },
        stream: false,
        response_format: None,
        tool_choice: None,
        store: None,
        previous_response_id: None,
        conversation: None,
        passthrough_fields: None,
        token_limit_field: None,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert!(
        v.get("store").is_none(),
        "未传 store 时不得出现在 wire body"
    );
}

#[test]
fn previous_response_id_reaches_wire_body() {
    // P2 review：续写 ID 必须透传——Core 能看到并决定接受/拒绝。
    let req = CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![serde_json::json!("继续")],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        }],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: Some(64),
        },
        stream: false,
        response_format: None,
        tool_choice: None,
        store: Some(false),
        previous_response_id: Some("resp_prior_turn".into()),
        conversation: None,
        passthrough_fields: None,
        token_limit_field: None,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(
        v["previous_response_id"], "resp_prior_turn",
        "续写 ID 必须原样到达 Responses wire body"
    );
}

#[test]
fn responses_omitted_token_limit_not_emitted() {
    let req = CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![serde_json::json!("hi")],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        }],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: None,
        },
        stream: false,
        response_format: None,
        tool_choice: None,
        store: None,
        previous_response_id: None,
        conversation: None,
        passthrough_fields: None,
        token_limit_field: None,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert!(v.get("max_output_tokens").is_none(), "未传时不出现");
}

#[test]
fn conversation_field_reaches_wire_body() {
    // hidb4ai P2：Responses 的 conversation 参数（与会话 ID 不同）
    // 也必须透传到上游载荷，让 Core 决定接受或拒绝。
    let req = CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![serde_json::json!("hi")],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        }],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: Some(64),
        },
        stream: false,
        response_format: None,
        tool_choice: None,
        store: None,
        previous_response_id: None,
        conversation: Some("conv_prior".into()),
        passthrough_fields: None,
        token_limit_field: None,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(
        v["conversation"], "conv_prior",
        "conversation 参数必须原样到达 Responses wire body"
    );
}
