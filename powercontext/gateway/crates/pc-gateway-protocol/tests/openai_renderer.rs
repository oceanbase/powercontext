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
    CanonicalMessage, CanonicalRequest, OpenAiChatRenderer, OutputLimits, Role, WireRenderer,
};

fn request() -> CanonicalRequest {
    CanonicalRequest {
        model: "m".into(),
        messages: vec![CanonicalMessage {
            role: Role::User,
            content: vec![serde_json::json!("hi")],
        }],
        tools: vec![],
        instructions: None,
        native_cache_fields: None,
        output_limits: OutputLimits {
            max_output_tokens: 16,
        },
        stream: false,
    }
}

#[test]
fn renders_deterministic_base_body() {
    let p = OpenAiChatRenderer.render(&request(), None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(v["model"], "m");
    assert_eq!(v["stream"], false);
    assert_eq!(v["max_tokens"], 16);
    // 缓存前缀保真：不注入请求 ID/时间戳类字段。
    assert!(v.get("request_id").is_none());
    assert!(v.get("timestamp").is_none());
}

#[test]
fn merges_only_approved_cache_plan_fields() {
    let plan = serde_json::json!({"cache_control": {"type": "ephemeral"}});
    let p = OpenAiChatRenderer.render(&request(), Some(&plan)).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(v["cache_control"]["type"], "ephemeral");
    assert_eq!(v["model"], "m", "基础字段不被计划覆盖");
}

#[test]
fn non_object_plan_rejected() {
    let plan = serde_json::json!(["not", "an", "object"]);
    let err = OpenAiChatRenderer.render(&request(), Some(&plan)).unwrap_err();
    assert!(matches!(
        err,
        pc_gateway_protocol::RenderError::InvalidRenderPlan { .. }
    ));
}

#[test]
fn stream_flag_reaches_wire_format() {
    let mut req = request();
    req.stream = true;
    let p = OpenAiChatRenderer.render(&req, None).unwrap();
    let v: serde_json::Value = serde_json::from_slice(&p.bytes).unwrap();
    assert_eq!(v["stream"], true, "stream 标志必须原样到达线格式");
}
