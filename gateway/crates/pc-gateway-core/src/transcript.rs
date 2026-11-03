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

/// 响应是回合的终止响应时，返回该回合的规范化记录。响应仍需客户端执行工具、含有无法识别的条目或无法解析时
/// 返回 `None`：宁可不沉淀，也不把工具循环的中间响应当成回合结束。
pub(crate) fn turn_transcript(
    request: &CanonicalRequest,
    response: &str,
    list_key: &str,
) -> Option<String> {
    let response: Value = serde_json::from_str(response).ok()?;
    let (final_steps, assistant) = if is_responses(list_key) {
        responses_final(&response)?
    } else {
        (Vec::new(), chat_final_text(&response)?)
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
                m.content.first().map(item_steps).unwrap_or_default()
            } else {
                chat_steps(m)
            }
        })
        .collect();
    steps.extend(final_steps);
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

/// Chat 终止响应：第一个 choice 的消息既没有 `tool_calls`，也没有旧式的 `function_call`。
fn chat_final_text(response: &Value) -> Option<String> {
    let message = response.get("choices")?.get(0)?.get("message")?;
    let calls = message.get("tool_calls").and_then(Value::as_array);
    if calls.is_some_and(|calls| !calls.is_empty())
        || message
            .get("function_call")
            .is_some_and(|call| !call.is_null())
    {
        return None;
    }
    Some(content_text(message.get("content")))
}

/// Responses 条目按语义分类。
#[derive(Debug, PartialEq)]
enum ItemKind {
    Message,
    /// 不进入记录、也不影响回合是否结束的条目：推理与 MCP 工具清单。
    Ignored,
    /// 服务商在同一响应内执行完毕的内置工具，结果随条目返回，客户端不需要再发请求。
    HostedCall,
    /// 需要客户端执行的工具调用（`*_call`、MCP 审批请求）。
    ClientCall,
    /// 客户端回传的工具结果（`*_call_output`、MCP 审批答复）。
    CallOutput,
    Unknown,
}

const HOSTED_CALLS: &[&str] = &[
    "web_search_call",
    "file_search_call",
    "code_interpreter_call",
    "image_generation_call",
    "mcp_call",
];

fn classify(item: &Value) -> ItemKind {
    match item
        .get("type")
        .and_then(Value::as_str)
        .unwrap_or("message")
    {
        "message" => ItemKind::Message,
        "reasoning" | "mcp_list_tools" => ItemKind::Ignored,
        kind if HOSTED_CALLS.contains(&kind) => ItemKind::HostedCall,
        kind if kind.ends_with("_call_output") || kind == "mcp_approval_response" => {
            ItemKind::CallOutput
        }
        kind if kind.ends_with("_call") || kind == "mcp_approval_request" => ItemKind::ClientCall,
        _ => ItemKind::Unknown,
    }
}

/// Responses 终止响应：output 只含助手消息、可忽略的条目和已在响应内完成的内置工具。
/// 需要客户端执行的调用或无法识别的条目都表示回合尚未结束。
fn responses_final(response: &Value) -> Option<(Vec<Step>, String)> {
    let output = response.get("output")?.as_array()?;
    let mut steps = Vec::new();
    let mut texts = Vec::new();
    for item in output {
        match classify(item) {
            ItemKind::Message => texts.extend(assistant_text(item)),
            ItemKind::Ignored => {}
            ItemKind::HostedCall => steps.extend(item_steps(item)),
            ItemKind::ClientCall | ItemKind::CallOutput | ItemKind::Unknown => return None,
        }
    }
    Some((steps, texts.join("\n")))
}

fn assistant_text(item: &Value) -> Option<String> {
    (item.get("role").and_then(Value::as_str) == Some("assistant"))
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

/// 一个 Responses 条目在记录中的步骤。工具调用取名称（没有名称时用条目类型）与参数；内置工具随条目返回的结果
/// 作为工具输出。条目 ID、`call_id`、状态等字段不进入记录。
fn item_steps(item: &Value) -> Vec<Step> {
    match classify(item) {
        ItemKind::Message => assistant_text(item)
            .filter(|text| !text.is_empty())
            .map(|text| {
                vec![Step::Assistant {
                    text: bounded(text),
                }]
            })
            .unwrap_or_default(),
        ItemKind::ClientCall | ItemKind::HostedCall => {
            let name = item
                .get("name")
                .and_then(Value::as_str)
                .or_else(|| item.get("type").and_then(Value::as_str))
                .unwrap_or_default()
                .to_owned();
            let arguments = first_field(
                item,
                &["arguments", "input", "action", "code", "query", "queries"],
            )
            .map(value_text)
            .unwrap_or_default();
            let mut steps = vec![Step::ToolCall {
                name,
                arguments: bounded(arguments),
            }];
            if let Some(output) = first_field(item, &["output", "outputs", "results"]) {
                steps.push(Step::ToolOutput {
                    output: bounded(value_text(output)),
                });
            }
            steps
        }
        ItemKind::CallOutput => vec![Step::ToolOutput {
            output: bounded(item.get("output").map(value_text).unwrap_or_default()),
        }],
        ItemKind::Ignored | ItemKind::Unknown => Vec::new(),
    }
}

fn first_field<'a>(item: &'a Value, keys: &[&str]) -> Option<&'a Value> {
    keys.iter()
        .filter_map(|key| item.get(*key))
        .find(|value| !value.is_null())
}

/// 字段的可读文本：字符串原样，内容块数组取其文本；其余结构序列化为 JSON，其中的 `data:` URL
/// （截图、图片等二进制内容）替换为占位文本。
fn value_text(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Array(parts) if !parts_text(parts).is_empty() => parts_text(parts),
        other => without_binary(other).to_string(),
    }
}

fn without_binary(value: &Value) -> Value {
    match value {
        Value::String(text) if text.starts_with("data:") => Value::from("[binary data omitted]"),
        Value::Array(items) => Value::Array(items.iter().map(without_binary).collect()),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, value)| (key.clone(), without_binary(value)))
                .collect(),
        ),
        other => other.clone(),
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

/// 规范化记录是否完整呈现了这一回合：没有截断或省略的内容，也没有记录无法呈现的部件（图片、文件、内置工具或
/// 无法识别的条目）。复用前缀抽取记忆时模型看到整个回合，只有记录完整时，Source 才覆盖模型作为证据看到的内容。
pub(crate) fn represents_turn(
    request: &CanonicalRequest,
    transcript: &str,
    list_key: &str,
) -> bool {
    let Ok(record) = serde_json::from_str::<Value>(transcript) else {
        return false;
    };
    if record.get("omitted_steps").is_some() || has_elided_text(&record) {
        return false;
    }
    let Some(start) = request
        .messages
        .iter()
        .rposition(|m| matches!(m.role, Role::User))
    else {
        return false;
    };
    request.messages[start..]
        .iter()
        .enumerate()
        .all(|(index, message)| {
            if index > 0 && is_responses(list_key) {
                message.content.first().is_some_and(textual_item)
            } else {
                message.content.iter().all(textual_part)
            }
        })
}

fn textual_part(part: &Value) -> bool {
    part.is_string()
        || matches!(
            part.get("type").and_then(Value::as_str),
            Some("text" | "input_text" | "output_text")
        )
}

fn textual_item(item: &Value) -> bool {
    match classify(item) {
        ItemKind::Ignored | ItemKind::ClientCall => true,
        ItemKind::Message => item.get("content").is_none_or(|content| match content {
            Value::Array(parts) => parts.iter().all(textual_part),
            other => other.is_string(),
        }),
        ItemKind::CallOutput => match item.get("output") {
            None | Some(Value::String(_)) => true,
            Some(Value::Array(parts)) => parts.iter().all(textual_part),
            Some(_) => false,
        },
        ItemKind::HostedCall | ItemKind::Unknown => false,
    }
}

/// 记录里是否有被截断或省略的文本（见 [`bounded`] 与 [`without_binary`] 的标记）。
fn has_elided_text(value: &Value) -> bool {
    match value {
        Value::String(text) => {
            (text.contains("\n[truncated ") && text.ends_with(" bytes]"))
                || text.contains("[binary data omitted]")
        }
        Value::Array(items) => items.iter().any(has_elided_text),
        Value::Object(map) => map.values().any(has_elided_text),
        _ => false,
    }
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
    fn built_in_calls_and_outputs_in_the_history_are_kept_without_binary_data() {
        let item = |value: Value| CanonicalMessage {
            role: Role::Tool,
            content: vec![value],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        };
        let mut request = chat_turn(&[]);
        request.messages.extend([
            item(serde_json::json!({"type": "computer_call", "id": "cu_1", "call_id": "call_1",
                "status": "completed", "action": {"type": "click", "x": 1, "y": 2}})),
            item(serde_json::json!({"type": "computer_call_output", "call_id": "call_1",
                "output": {"type": "computer_screenshot", "image_url": "data:image/png;base64,AAAA"}})),
            item(serde_json::json!({"type": "mcp_call", "id": "mcp_1", "name": "search_docs",
                "server_label": "docs", "arguments": "{\"q\":\"loader\"}", "output": "3 results"})),
            item(serde_json::json!({"type": "reasoning", "encrypted_content": "gAAAA"})),
            item(serde_json::json!({"type": "frobnicate", "data": "opaque"})),
        ]);
        let response = r#"{"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}]}"#;
        let text = turn_transcript(&request, response, "input").unwrap();
        let transcript: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(
            transcript["steps"],
            serde_json::json!([
                {"type": "tool_call", "name": "computer_call", "arguments": "{\"type\":\"click\",\"x\":1,\"y\":2}"},
                {"type": "tool_output", "output": "{\"image_url\":\"[binary data omitted]\",\"type\":\"computer_screenshot\"}"},
                {"type": "tool_call", "name": "search_docs", "arguments": "{\"q\":\"loader\"}"},
                {"type": "tool_output", "output": "3 results"}
            ])
        );
        for absent in ["call_1", "cu_1", "mcp_1", "base64", "gAAAA", "opaque"] {
            assert!(!text.contains(absent), "{absent} in {text}");
        }
    }

    #[test]
    fn only_a_lossless_record_represents_the_turn() {
        let item = |value: Value| CanonicalMessage {
            role: Role::Tool,
            content: vec![value],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
        };
        let response = r#"{"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}]}"#;
        let represented = |extra: Option<Value>| {
            let mut request = chat_turn(&[]);
            request.messages.extend([
                item(serde_json::json!({"type": "function_call", "call_id": "c", "name": "run", "arguments": "{}"})),
                item(serde_json::json!({"type": "function_call_output", "call_id": "c", "output": "ok"})),
                item(serde_json::json!({"type": "reasoning", "encrypted_content": "gAAAA"})),
            ]);
            request.messages.extend(extra.map(item));
            let text = turn_transcript(&request, response, "input").unwrap();
            represents_turn(&request, &text, "input")
        };
        assert!(represented(None));
        for lossy in [
            serde_json::json!({"type": "computer_call_output", "call_id": "c",
                "output": {"type": "computer_screenshot", "image_url": "data:image/png;base64,AAAA"}}),
            serde_json::json!({"type": "function_call_output", "call_id": "c",
                "output": [{"type": "input_image", "image_url": "https://example.com/a.png"}]}),
            serde_json::json!({"type": "mcp_call", "name": "search_docs", "server_label": "docs",
                "arguments": "{}", "output": "3 results"}),
            serde_json::json!({"type": "frobnicate", "data": "opaque"}),
            serde_json::json!({"type": "function_call_output", "call_id": "c", "output": "x".repeat(MAX_FIELD_BYTES + 1)}),
        ] {
            assert!(!represented(Some(lossy.clone())), "{lossy}");
        }
    }

    #[test]
    fn client_and_unknown_items_keep_a_responses_turn_open() {
        let request = chat_turn(&[]);
        for item in [
            r#"{"type":"computer_call","call_id":"c","action":{"type":"screenshot"}}"#,
            r#"{"type":"local_shell_call","call_id":"c","action":{"command":["ls"]}}"#,
            r#"{"type":"mcp_approval_request","name":"delete","server_label":"s","arguments":"{}"}"#,
            r#"{"type":"frobnicate"}"#,
        ] {
            let response = format!(r#"{{"output":[{item}]}}"#);
            assert_eq!(
                turn_transcript(&request, &response, "input"),
                None,
                "{item}"
            );
        }
        let legacy = r#"{"choices":[{"message":{"role":"assistant","content":null,
            "function_call":{"name":"read","arguments":"{}"}}}]}"#;
        assert_eq!(turn_transcript(&request, legacy, "messages"), None);
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
