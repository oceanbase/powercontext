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

//! 一个用户回合的规范化记录，供网关在回合结束时沉淀一次。
//!
//! 回合从最后一条用户消息开始，到不再调用工具的响应（终止响应）结束。记录只保留模型可见的对话：
//! 用户文本、工具调用（名称与参数）、工具输出和助手文本；推理密文、各类 ID、用量和其他传输元数据都不进入记录。
//! 用户文本取自注入前的请求，不含准备结果。

use pc_gateway_protocol::{CanonicalMessage, CanonicalRequest, Role};
use serde::Serialize;
use serde_json::Value;

/// 单个字段（用户文本、工具参数、工具输出、助手文本）的字节上限，超出部分截断。
pub(crate) const MAX_FIELD_BYTES: usize = 8 * 1024;
/// 整条记录的字节上限：超出时从最早的步骤开始省略。
pub(crate) const MAX_TRANSCRIPT_BYTES: usize = 64 * 1024;

#[derive(Debug, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum Step {
    ToolCall { name: String, arguments: String },
    ToolOutput { output: String },
    Assistant { text: String },
}

#[derive(Debug, Serialize)]
struct Transcript {
    kind: &'static str,
    user: String,
    steps: Vec<Step>,
    #[serde(skip_serializing_if = "is_zero")]
    omitted_steps: usize,
    assistant: String,
}

fn is_zero(n: &usize) -> bool {
    *n == 0
}

/// 渲染后消息列表所在的顶层键决定线格式：`input` 为 Responses，其余为 Chat。
fn is_responses(list_key: &str) -> bool {
    list_key == "input"
}

/// 响应是回合的终止响应时，返回该回合的规范化记录；响应仍在调用工具或无法解析时返回 `None`。
pub(crate) fn turn_transcript(
    request: &CanonicalRequest,
    response: &str,
    list_key: &str,
) -> Option<String> {
    let response: Value = serde_json::from_str(response).ok()?;
    let assistant = if is_responses(list_key) {
        responses_final_text(&response)?
    } else {
        chat_final_text(&response)?
    };
    let start = request
        .messages
        .iter()
        .rposition(|m| matches!(m.role, Role::User))?;
    let user = parts_text(&request.messages[start].content);
    let mut steps: Vec<Step> = request.messages[start + 1..]
        .iter()
        .flat_map(|m| {
            if is_responses(list_key) {
                responses_steps(m)
            } else {
                chat_steps(m)
            }
        })
        .collect();
    let mut transcript = Transcript {
        kind: "gateway_turn",
        user: bounded(user),
        steps: Vec::new(),
        omitted_steps: 0,
        assistant: bounded(assistant),
    };
    loop {
        transcript.steps = std::mem::take(&mut steps);
        let text = serde_json::to_string(&transcript).ok()?;
        if text.len() <= MAX_TRANSCRIPT_BYTES || transcript.steps.is_empty() {
            return Some(text);
        }
        steps = std::mem::take(&mut transcript.steps);
        let drop = (steps.len() / 4).max(1);
        steps.drain(..drop);
        transcript.omitted_steps += drop;
    }
}

/// Chat 终止响应：第一个 choice 的消息没有工具调用。
fn chat_final_text(response: &Value) -> Option<String> {
    let message = response.get("choices")?.get(0)?.get("message")?;
    let calls = message.get("tool_calls").and_then(Value::as_array);
    if calls.is_some_and(|calls| !calls.is_empty()) {
        return None;
    }
    Some(match message.get("content") {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Array(parts)) => parts_text(parts),
        _ => String::new(),
    })
}

/// Responses 终止响应：output 中没有工具调用条目。
fn responses_final_text(response: &Value) -> Option<String> {
    let output = response.get("output")?.as_array()?;
    if output.iter().any(is_responses_tool_call) {
        return None;
    }
    let texts: Vec<String> = output.iter().filter_map(responses_assistant_text).collect();
    Some(texts.join("\n"))
}

fn is_responses_tool_call(item: &Value) -> bool {
    matches!(
        item.get("type").and_then(Value::as_str),
        Some("function_call" | "custom_tool_call")
    )
}

fn responses_assistant_text(item: &Value) -> Option<String> {
    let is_message = item
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("message")
        == "message";
    (is_message && item.get("role").and_then(Value::as_str) == Some("assistant"))
        .then(|| content_text(item.get("content")))
}

fn chat_steps(message: &CanonicalMessage) -> Vec<Step> {
    match message.role {
        Role::Assistant => {
            let mut steps = Vec::new();
            let text = parts_text(&message.content);
            if !text.is_empty() {
                steps.push(Step::Assistant {
                    text: bounded(text),
                });
            }
            for call in message.tool_calls.iter().flatten() {
                let function = call.get("function");
                steps.push(Step::ToolCall {
                    name: string_field(function, "name"),
                    arguments: bounded(string_field(function, "arguments")),
                });
            }
            steps
        }
        Role::Tool => vec![Step::ToolOutput {
            output: bounded(parts_text(&message.content)),
        }],
        _ => Vec::new(),
    }
}

/// Responses 入口把用户消息以外的条目原样封装为单元素内容，这里按条目类型取出可见部分。
fn responses_steps(message: &CanonicalMessage) -> Vec<Step> {
    let Some(item) = message.content.first() else {
        return Vec::new();
    };
    let kind = item.get("type").and_then(Value::as_str);
    match kind {
        Some("function_call") => vec![Step::ToolCall {
            name: string_field(Some(item), "name"),
            arguments: bounded(string_field(Some(item), "arguments")),
        }],
        Some("custom_tool_call") => vec![Step::ToolCall {
            name: string_field(Some(item), "name"),
            arguments: bounded(string_field(Some(item), "input")),
        }],
        Some("function_call_output" | "custom_tool_call_output") => vec![Step::ToolOutput {
            output: bounded(content_text(item.get("output"))),
        }],
        _ => responses_assistant_text(item)
            .filter(|text| !text.is_empty())
            .map(|text| {
                vec![Step::Assistant {
                    text: bounded(text),
                }]
            })
            .unwrap_or_default(),
    }
}

fn string_field(value: Option<&Value>, key: &str) -> String {
    value
        .and_then(|v| v.get(key))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned()
}

/// 字符串或内容块数组中的文本。
fn content_text(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Array(parts)) => parts_text(parts),
        _ => String::new(),
    }
}

fn parts_text(parts: &[Value]) -> String {
    let texts: Vec<&str> = parts
        .iter()
        .filter_map(|part| match part {
            Value::String(text) => Some(text.as_str()),
            _ if matches!(
                part.get("type").and_then(Value::as_str),
                Some("text" | "input_text" | "output_text")
            ) =>
            {
                part.get("text").and_then(Value::as_str)
            }
            _ => None,
        })
        .collect();
    texts.join("\n")
}

/// 截断到 [`MAX_FIELD_BYTES`]（按字符边界），并注明省略的字节数。
fn bounded(text: String) -> String {
    if text.len() <= MAX_FIELD_BYTES {
        return text;
    }
    let mut end = MAX_FIELD_BYTES;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}\n[truncated {} bytes]", &text[..end], text.len() - end)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn long_fields_are_truncated_on_a_character_boundary() {
        let text = "é".repeat(MAX_FIELD_BYTES);
        let out = bounded(text.clone());
        assert!(out.len() < text.len());
        assert!(out.ends_with(&format!(
            "[truncated {} bytes]",
            text.len() - MAX_FIELD_BYTES
        )));
        assert_eq!(bounded("short".into()), "short");
    }

    fn chat_turn(outputs: &[String]) -> CanonicalRequest {
        let message = |role, content: Vec<Value>, tool_calls| CanonicalMessage {
            role,
            content,
            content_null: false,
            tool_calls,
            tool_call_id: None,
        };
        let mut messages = vec![message(Role::User, vec![Value::from("task")], None)];
        for output in outputs {
            messages.push(message(
                Role::Assistant,
                Vec::new(),
                Some(vec![
                    serde_json::json!({"function": {"name": "read", "arguments": "{}"}}),
                ]),
            ));
            messages.push(message(
                Role::Tool,
                vec![Value::from(output.as_str())],
                None,
            ));
        }
        serde_json::from_value(serde_json::json!({
            "model": "m",
            "messages": messages,
            "output_limits": {"max_output_tokens": null}
        }))
        .unwrap()
    }

    const FINAL: &str = r#"{"choices":[{"message":{"role":"assistant","content":"done"}}]}"#;

    #[test]
    fn a_long_tool_output_is_truncated() {
        let request = chat_turn(&["x".repeat(MAX_FIELD_BYTES * 2)]);
        let text = turn_transcript(&request, FINAL, "messages").unwrap();
        let transcript: Value = serde_json::from_str(&text).unwrap();
        let output = transcript["steps"][1]["output"].as_str().unwrap();
        assert!(output.ends_with(&format!("[truncated {MAX_FIELD_BYTES} bytes]")));
        assert_eq!(transcript["assistant"], "done");
    }

    #[test]
    fn an_oversized_turn_omits_its_oldest_steps() {
        let outputs: Vec<String> = (0..40)
            .map(|i| format!("{i:02}{}", "x".repeat(MAX_FIELD_BYTES - 2)))
            .collect();
        let request = chat_turn(&outputs);
        let text = turn_transcript(&request, FINAL, "messages").unwrap();
        assert!(text.len() <= MAX_TRANSCRIPT_BYTES);
        let transcript: Value = serde_json::from_str(&text).unwrap();
        let omitted = transcript["omitted_steps"].as_u64().unwrap();
        assert!(omitted > 0);
        let steps = transcript["steps"].as_array().unwrap();
        // 保留的是最近的步骤，最后一步是最后一次工具输出。
        assert!(steps.last().unwrap()["output"]
            .as_str()
            .unwrap()
            .starts_with("39"));
        assert_eq!(steps.len() as u64 + omitted, 80);
        assert_eq!(transcript["user"], "task");
        assert_eq!(transcript["assistant"], "done");
    }

    #[test]
    fn a_tool_calling_or_unparsable_response_is_not_terminal() {
        let request = chat_turn(&[]);
        let calling = r#"{"choices":[{"message":{"role":"assistant","content":null,
            "tool_calls":[{"function":{"name":"read","arguments":"{}"}}]}}]}"#;
        assert_eq!(turn_transcript(&request, calling, "messages"), None);
        assert_eq!(turn_transcript(&request, "not json", "messages"), None);
        let responses_calling =
            r#"{"output":[{"type":"custom_tool_call","name":"exec","input":"ls"}]}"#;
        assert_eq!(turn_transcript(&request, responses_calling, "input"), None);
    }
}
