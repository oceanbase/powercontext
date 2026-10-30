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
    CanonicalMessage, CanonicalRequest, OpenAiResponsesRenderer, OutputLimits, Role,
    WireRenderer,
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
            max_output_tokens: 16,
        },
        stream: false,
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
            max_output_tokens: 16,
        },
        stream: false,
    };
    let p = OpenAiResponsesRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(v["input"].as_array().unwrap().len(), 2);
    assert_eq!(v["input"][1], fc, "Responses 专有 item 必须字节级透传");
}

#[test]
fn merges_only_approved_cache_plan_fields() {
    let plan = serde_json::json!({"prompt_cache_key": "scope-1"});
    let p = OpenAiResponsesRenderer.render(&request(), Some(&plan)).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(v["prompt_cache_key"], "scope-1");
    assert_eq!(v["model"], "m", "基础字段不被计划覆盖");
}
