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
use crate::{CanonicalMessage, CanonicalRequest, OutputLimits, Role};
use serde::Deserialize;
use serde_json::{json, Map, Value};

#[derive(Deserialize)]
struct MessagesRequest {
    model: String,
    messages: Vec<Message>,
    max_tokens: u32,
    #[serde(default)]
    stream: bool,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

#[derive(Deserialize)]
struct Message {
    role: MessageRole,
    content: Content,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

#[derive(Deserialize)]
#[serde(rename_all = "lowercase")]
enum MessageRole {
    User,
    Assistant,
    System,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Content {
    Text(String),
    Blocks(Vec<Map<String, Value>>),
}

/// Preserve native fields and blocks, while requesting one complete JSON response for Core.
pub fn parse_messages(
    body: &[u8],
    steering: Option<&str>,
) -> Result<(CanonicalRequest, bool), &'static str> {
    let mut request: MessagesRequest =
        serde_json::from_slice(body).map_err(|_| "invalid_messages_body")?;
    if let Some(steering) = steering {
        let block = json!({"type": "text", "text": steering});
        let system = request.extra.entry("system").or_insert_with(|| json!([]));
        match system {
            Value::Array(blocks) => blocks.push(block),
            Value::String(text) => {
                *system = json!([{"type": "text", "text": text}, block]);
            }
            _ => return Err("invalid_messages_system"),
        }
    }
    let messages = request
        .messages
        .into_iter()
        .map(|message| CanonicalMessage {
            role: match message.role {
                MessageRole::User => Role::User,
                MessageRole::Assistant => Role::Assistant,
                MessageRole::System => Role::System,
            },
            content: match message.content {
                Content::Text(text) => vec![Value::String(text)],
                Content::Blocks(blocks) => blocks.into_iter().map(Value::Object).collect(),
            },
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
            extra_fields: message.extra,
        })
        .collect();
    Ok((
        CanonicalRequest {
            model: request.model,
            messages,
            tools: Vec::new(),
            instructions: None,
            native_cache_fields: None,
            output_limits: OutputLimits {
                max_output_tokens: Some(request.max_tokens),
            },
            stream: false,
            response_format: None,
            tool_choice: None,
            store: None,
            token_limit_field: Some("max_tokens".into()),
            previous_response_id: None,
            conversation: None,
            passthrough_fields: Some(request.extra),
        },
        request.stream,
    ))
}

/// Reconstruct an Anthropic event stream from a complete, approved Message.
pub fn messages_sse(mut response: Value) -> Option<String> {
    let message = response.as_object_mut()?;
    if message.get("type")?.as_str()? != "message" || message.get("role")?.as_str()? != "assistant"
    {
        return None;
    }
    message.get("id")?.as_str()?;
    message.get("model")?.as_str()?;
    message.get("stop_reason")?.as_str()?;
    let Value::Array(blocks) = message.insert("content".into(), json!([]))? else {
        return None;
    };
    let mut terminal = Map::new();
    for key in ["stop_reason", "stop_sequence", "stop_details"] {
        if let Some(value) = message.get_mut(key) {
            terminal.insert(key.into(), std::mem::take(value));
        }
    }
    let usage = message.get_mut("usage")?.as_object_mut()?;
    usage.get("output_tokens")?.as_u64()?;
    let final_usage = Value::Object(usage.clone());
    usage.insert("output_tokens".into(), json!(0));

    let mut out = String::new();
    let mut push = |event: &str, mut data: Value| {
        data["type"] = json!(event);
        out.push_str("event: ");
        out.push_str(event);
        out.push_str("\ndata: ");
        out.push_str(&data.to_string());
        out.push_str("\n\n");
    };
    push("message_start", json!({"message": response}));
    for (index, mut block) in blocks.into_iter().enumerate() {
        let mut deltas = Vec::new();
        match block.get("type")?.as_str()? {
            "text" => {
                let text = block.get_mut("text")?;
                deltas.push(json!({"type": "text_delta", "text": text.as_str()?}));
                *text = json!("");
                if let Some(Value::Array(citations)) = block.get_mut("citations") {
                    deltas.extend(
                        std::mem::take(citations).into_iter().map(
                            |citation| json!({"type": "citations_delta", "citation": citation}),
                        ),
                    );
                }
            }
            "tool_use" | "server_tool_use" | "mcp_tool_use" => {
                let input = block.get_mut("input")?;
                deltas.push(json!({"type": "input_json_delta", "partial_json": input.to_string()}));
                *input = json!({});
            }
            "thinking" => {
                let thinking = block.get_mut("thinking")?;
                deltas.push(json!({"type": "thinking_delta", "thinking": thinking.as_str()?}));
                *thinking = json!("");
                let signature = block.get_mut("signature")?;
                deltas.push(json!({"type": "signature_delta", "signature": signature.as_str()?}));
                *signature = json!("");
            }
            // Atomic blocks, including opaque reasoning and provider tool results, have no deltas.
            _ => {}
        }
        push(
            "content_block_start",
            json!({"index": index, "content_block": block}),
        );
        for delta in deltas {
            push(
                "content_block_delta",
                json!({"index": index, "delta": delta}),
            );
        }
        push("content_block_stop", json!({"index": index}));
    }
    push(
        "message_delta",
        json!({"delta": terminal, "usage": final_usage}),
    );
    push("message_stop", json!({}));
    Some(out)
}
