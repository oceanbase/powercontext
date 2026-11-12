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

//! 一个用户回合的规范化记录，供网关在回合结束时沉淀。
//!
//! 回合从最后一条用户消息开始，到不再调用工具的响应（终止响应）结束。记录完整保留这一回合里模型读到的内容：
//! 用户文本、其后的其他消息、工具调用的全部参数字段、工具输出与错误、推理的可见摘要、无法识别的条目，以及助手
//! 文本；字段不截断，步骤不省略。只有推理密文、各类 ID、状态、用量等传输元数据不进入记录，二进制数据（截图、
//! 图片、文件）记为带字节数的占位文本。用户文本取自注入前的请求，不含准备结果。
//!
//! Source 正文有长度上限（[`MAX_SOURCE_CHARS`]）。超出时，记录按顺序拆成几段，每段都是独立的 Source。

use crate::capture_coverage::declaration;
use pc_gateway_protocol::{CanonicalMessage, CanonicalRequest, Role};
use serde::Serialize;
use serde_json::{Map, Value};

/// 投影名称，见捕获覆盖声明契约。
pub(crate) const PROJECTION: &str = "gateway-turn-text.v2";
/// 一个 Source 正文的字符数上限（契约中 `CaptureContentSourceRequest.content` 的 `maxLength`）。
pub(crate) const MAX_SOURCE_CHARS: usize = 200_000;

#[derive(Debug, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum Step {
    ToolCall {
        name: String,
        arguments: String,
    },
    ToolOutput {
        output: String,
    },
    Assistant {
        text: String,
    },
    /// 用户消息之后、非助手角色的消息（如 developer、system）。
    Message {
        role: String,
        text: String,
    },
    /// 推理的可见摘要；推理密文不进入记录。
    ReasoningSummary {
        text: String,
    },
    /// 无法识别类型的条目，去掉 ID、状态和二进制数据后原样记录。
    Item {
        item_type: String,
        content: String,
    },
}

#[derive(Debug, Serialize)]
struct Transcript {
    kind: &'static str,
    user: String,
    steps: Vec<Step>,
    assistant: String,
}

/// 渲染后消息列表所在的顶层键决定线格式：`input` 为 Responses，其余为 Chat。
fn is_responses(list_key: &str) -> bool {
    list_key == "input"
}

/// 回合的记录：放得下时是一段，否则按顺序拆成几段；以及投影的覆盖声明。
#[derive(Debug, PartialEq)]
pub(crate) struct TurnProjection {
    pub parts: Vec<String>,
    pub coverage: Value,
}

/// 响应是回合的终止响应时，返回该回合的规范化记录。响应仍需客户端执行工具、含有无法识别的条目或无法解析时
/// 返回 `None`：宁可不沉淀，也不把工具循环的中间响应当成回合结束。
pub(crate) fn turn_transcript(
    request: &CanonicalRequest,
    response: &str,
    list_key: &str,
) -> Option<TurnProjection> {
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
                m.content.iter().flat_map(item_steps).collect()
            } else {
                chat_steps(m)
            }
        })
        .collect();
    steps.extend(final_steps);
    let transcript = Transcript {
        kind: "gateway_turn",
        user,
        steps,
        assistant,
    };
    Some(TurnProjection {
        parts: split(&transcript)?,
        coverage: declaration(PROJECTION, &[]),
    })
}

/// 整条记录放得下时原样返回一段。否则拆成按顺序编号的几段：每段是
/// `{"kind": "gateway_turn", "part": i, "parts": n, "steps": [...]}`，用户文本作为第一段的第一个步骤（`user`），
/// 助手文本作为最后一段的最后一个步骤；单个步骤放不下时，它最长的文本字段按字符拆成连续的几个同类步骤，
/// 后续部分带 `"continued": true`。
fn split(transcript: &Transcript) -> Option<Vec<String>> {
    let whole = serde_json::to_string(transcript).ok()?;
    if whole.chars().count() <= MAX_SOURCE_CHARS {
        return Some(vec![whole]);
    }
    let mut entries = vec![serde_json::json!({"type": "user", "text": transcript.user})];
    for step in &transcript.steps {
        entries.push(serde_json::to_value(step).ok()?);
    }
    entries.push(serde_json::json!({"type": "assistant", "text": transcript.assistant}));
    // 段的外层（kind、part、parts、括号与分隔符）留出余量。
    let budget = MAX_SOURCE_CHARS - 200;
    let pieces: Vec<Value> = entries
        .into_iter()
        .flat_map(|entry| fit(entry, budget))
        .collect();
    let mut groups: Vec<Vec<Value>> = vec![Vec::new()];
    let mut used = 0;
    for piece in pieces {
        let size = piece.to_string().chars().count() + 1;
        if used + size > budget && !groups.last().is_some_and(Vec::is_empty) {
            groups.push(Vec::new());
            used = 0;
        }
        used += size;
        groups.last_mut()?.push(piece);
    }
    let count = groups.len();
    groups
        .into_iter()
        .enumerate()
        .map(|(index, steps)| {
            serde_json::to_string(&serde_json::json!({
                "kind": "gateway_turn",
                "part": index + 1,
                "parts": count,
                "steps": steps,
            }))
            .ok()
        })
        .collect()
}

/// 把一个放不下的步骤按其最长的字符串字段拆成连续的几个步骤，每个都不超过 `budget` 个字符。
fn fit(entry: Value, budget: usize) -> Vec<Value> {
    if entry.to_string().chars().count() < budget {
        return vec![entry];
    }
    let Some(fields) = entry.as_object() else {
        return vec![entry];
    };
    let Some((key, text)) = fields
        .iter()
        .filter_map(|(key, value)| value.as_str().map(|text| (key.clone(), text.to_owned())))
        .max_by_key(|(_, text)| text.len())
    else {
        return vec![entry];
    };
    let mut shell = fields.clone();
    shell.insert(key.clone(), Value::from(""));
    shell.insert("continued".into(), Value::from(true));
    // JSON 转义最多把一个字符变成 6 个（`\uXXXX`），按最坏情况取每块的字符数。
    let room = budget.saturating_sub(Value::Object(shell).to_string().chars().count()) / 6;
    let chars: Vec<char> = text.chars().collect();
    chars
        .chunks(room.max(1))
        .enumerate()
        .map(|(index, chunk)| {
            let mut piece = fields.clone();
            piece.insert(key.clone(), Value::from(chunk.iter().collect::<String>()));
            if index > 0 {
                piece.insert("continued".into(), Value::from(true));
            }
            Value::Object(piece)
        })
        .collect()
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
    /// 推理条目：密文不进入记录，可见摘要记为步骤；不影响回合是否结束。
    Reasoning,
    /// MCP 工具清单：不进入记录，也不影响回合是否结束。
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
        "reasoning" => ItemKind::Reasoning,
        "mcp_list_tools" => ItemKind::Ignored,
        kind if HOSTED_CALLS.contains(&kind) => ItemKind::HostedCall,
        kind if kind.ends_with("_call_output") || kind == "mcp_approval_response" => {
            ItemKind::CallOutput
        }
        kind if kind.ends_with("_call") || kind == "mcp_approval_request" => ItemKind::ClientCall,
        _ => ItemKind::Unknown,
    }
}

/// Responses 终止响应：output 只含助手消息、推理、可忽略的条目和已在响应内完成的内置工具。
/// 需要客户端执行的调用或无法识别的条目都表示回合尚未结束。
fn responses_final(response: &Value) -> Option<(Vec<Step>, String)> {
    let output = response.get("output")?.as_array()?;
    let mut steps = Vec::new();
    let mut texts = Vec::new();
    for item in output {
        match classify(item) {
            ItemKind::Message => texts.extend(assistant_text(item)),
            ItemKind::Reasoning | ItemKind::Ignored | ItemKind::HostedCall => {
                steps.extend(item_steps(item))
            }
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
                steps.push(Step::Assistant { text });
            }
            for call in message.tool_calls.iter().flatten() {
                let function = call.get("function");
                let named = function
                    .and_then(|value| value.get("name"))
                    .and_then(Value::as_str);
                let arguments = function.and_then(|value| value.get("arguments"));
                // 标准函数调用记名称与参数字符串；其他形态的调用整体记录。
                let (name, arguments) = match (named, arguments.and_then(Value::as_str)) {
                    (Some(name), Some(arguments)) => (name.to_owned(), arguments.to_owned()),
                    _ => (
                        named
                            .or_else(|| call.get("type").and_then(Value::as_str))
                            .unwrap_or_default()
                            .to_owned(),
                        without_binary(&drop_keys(call, &["id"])).to_string(),
                    ),
                };
                steps.push(Step::ToolCall { name, arguments });
            }
            steps
        }
        Role::Tool => vec![Step::ToolOutput {
            output: parts_text(&message.content),
        }],
        Role::User => Vec::new(),
        _ => {
            let text = parts_text(&message.content);
            (!text.is_empty())
                .then(|| Step::Message {
                    role: role_name(&message.role),
                    text,
                })
                .into_iter()
                .collect()
        }
    }
}

fn role_name(role: &Role) -> String {
    serde_json::to_value(role)
        .ok()
        .and_then(|value| value.as_str().map(str::to_owned))
        .unwrap_or_default()
}

/// 调用条目里不属于参数的字段：标识、状态、名称和随条目返回的结果。
const CALL_METADATA: &[&str] = &["id", "call_id", "status", "type", "name"];
/// 内置工具随条目返回的结果字段。
const CALL_RESULTS: &[&str] = &["output", "outputs", "results", "error"];

/// 一个 Responses 条目在记录中的步骤。工具调用取名称（没有名称时用条目类型）和全部参数字段：只有一个参数字段时
/// 记它的文本，否则记这些字段组成的 JSON 对象；内置工具随条目返回的结果与错误作为工具输出。条目 ID、`call_id`、
/// 状态等字段不进入记录。
fn item_steps(item: &Value) -> Vec<Step> {
    match classify(item) {
        ItemKind::Message => {
            let role = item
                .get("role")
                .and_then(Value::as_str)
                .unwrap_or("assistant");
            let text = content_text(item.get("content"));
            if text.is_empty() {
                Vec::new()
            } else if role == "assistant" {
                vec![Step::Assistant { text }]
            } else {
                vec![Step::Message {
                    role: role.to_owned(),
                    text,
                }]
            }
        }
        ItemKind::Reasoning => {
            let text = item
                .get("summary")
                .and_then(Value::as_array)
                .map(|parts| parts_text(parts))
                .unwrap_or_default();
            (!text.is_empty())
                .then_some(Step::ReasoningSummary { text })
                .into_iter()
                .collect()
        }
        ItemKind::ClientCall | ItemKind::HostedCall => {
            let name = item
                .get("name")
                .and_then(Value::as_str)
                .or_else(|| item.get("type").and_then(Value::as_str))
                .unwrap_or_default()
                .to_owned();
            let excluded: Vec<&str> = CALL_METADATA.iter().chain(CALL_RESULTS).copied().collect();
            let mut steps = vec![Step::ToolCall {
                name,
                arguments: fields_text(&drop_keys(item, &excluded)),
            }];
            let results = keep_keys(item, CALL_RESULTS);
            if results.as_object().is_some_and(|fields| !fields.is_empty()) {
                steps.push(Step::ToolOutput {
                    output: fields_text(&results),
                });
            }
            steps
        }
        ItemKind::CallOutput => vec![Step::ToolOutput {
            output: fields_text(&drop_keys(item, &["id", "call_id", "status", "type"])),
        }],
        ItemKind::Ignored => Vec::new(),
        ItemKind::Unknown => vec![Step::Item {
            item_type: item
                .get("type")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            content: without_binary(&drop_keys(item, &["id", "status", "type"])).to_string(),
        }],
    }
}

/// 一组字段的文本：只有一个非空字段时记它的文本，否则记整个对象（二进制数据换成占位文本）。
fn fields_text(fields: &Value) -> String {
    let present: Vec<&Value> = fields
        .as_object()
        .map(|map| map.values().filter(|value| !value.is_null()).collect())
        .unwrap_or_default();
    match present.as_slice() {
        [] => String::new(),
        [value] => value_text(value),
        _ => without_binary(fields).to_string(),
    }
}

fn drop_keys(item: &Value, keys: &[&str]) -> Value {
    let fields: Map<String, Value> = item
        .as_object()
        .map(|map| {
            map.iter()
                .filter(|(key, _)| !keys.contains(&key.as_str()))
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect()
        })
        .unwrap_or_default();
    Value::Object(fields)
}

fn keep_keys(item: &Value, keys: &[&str]) -> Value {
    let fields: Map<String, Value> = item
        .as_object()
        .map(|map| {
            map.iter()
                .filter(|(key, value)| keys.contains(&key.as_str()) && !value.is_null())
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect()
        })
        .unwrap_or_default();
    Value::Object(fields)
}

/// 字段的可读文本：字符串原样，内容块数组取其文本；其余结构序列化为 JSON，其中的 `data:` URL
/// （截图、图片等二进制内容）替换为占位文本。
fn value_text(value: &Value) -> String {
    match value {
        Value::String(text) if text.starts_with("data:") => binary_placeholder(text),
        Value::String(text) => text.clone(),
        Value::Array(parts) if !parts_text(parts).is_empty() => parts_text(parts),
        other => without_binary(other).to_string(),
    }
}

fn without_binary(value: &Value) -> Value {
    match value {
        Value::String(text) if text.starts_with("data:") => Value::from(binary_placeholder(text)),
        Value::Array(items) => Value::Array(items.iter().map(without_binary).collect()),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, value)| (key.clone(), without_binary(value)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn binary_placeholder(data_url: &str) -> String {
    let media = data_url
        .strip_prefix("data:")
        .and_then(|rest| rest.split([';', ',']).next())
        .filter(|media| !media.is_empty())
        .unwrap_or("binary");
    format!("[{media} data omitted: {} bytes]", data_url.len())
}

/// 字符串或内容块数组中的文本。
fn content_text(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Array(parts)) => parts_text(parts),
        _ => String::new(),
    }
}

/// 内容块的文本。图片、文件等非文本块记为占位文本：有 URL 或文件名时带上，内联数据只记字节数。
fn parts_text(parts: &[Value]) -> String {
    let texts: Vec<String> = parts
        .iter()
        .filter_map(|part| match part {
            Value::String(text) => Some(text.clone()),
            _ => match part.get("type").and_then(Value::as_str) {
                Some("text" | "input_text" | "output_text" | "summary_text") => {
                    part.get("text").and_then(Value::as_str).map(str::to_owned)
                }
                Some(kind) => Some(match first_text(part, &["image_url", "file_url", "url"]) {
                    Some(url) if url.starts_with("data:") => binary_placeholder(&url),
                    Some(url) => format!("[{kind}: {url}]"),
                    None => match first_text(part, &["filename", "file_id"]) {
                        Some(name) => format!("[{kind}: {name}]"),
                        None => format!("[{kind}]"),
                    },
                }),
                None => None,
            },
        })
        .collect();
    texts.join("\n")
}

fn first_text(part: &Value, keys: &[&str]) -> Option<String> {
    keys.iter().find_map(|key| match part.get(*key) {
        Some(Value::String(text)) => Some(text.clone()),
        Some(Value::Object(inner)) => inner.get("url").and_then(Value::as_str).map(str::to_owned),
        _ => None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chat_turn(outputs: &[String]) -> CanonicalRequest {
        let message = |role, content: Vec<Value>, tool_calls| CanonicalMessage {
            role,
            content,
            content_null: false,
            tool_calls,
            extra_fields: Default::default(),
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

    fn item(value: Value) -> CanonicalMessage {
        CanonicalMessage {
            role: Role::Tool,
            content: vec![value],
            content_null: false,
            tool_calls: None,
            extra_fields: Default::default(),
            tool_call_id: None,
        }
    }

    const FINAL: &str = r#"{"choices":[{"message":{"role":"assistant","content":"done"}}]}"#;
    const RESPONSES_FINAL: &str = r#"{"output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"done"}]}]}"#;

    fn single(projection: &TurnProjection) -> Value {
        assert_eq!(projection.parts.len(), 1);
        serde_json::from_str(&projection.parts[0]).unwrap()
    }

    #[test]
    fn a_turn_is_recorded_in_full_and_declares_the_complete_projection() {
        let long = "文".repeat(50_000);
        let projection =
            turn_transcript(&chat_turn(std::slice::from_ref(&long)), FINAL, "messages").unwrap();
        let transcript = single(&projection);
        assert_eq!(transcript["user"], "task");
        assert_eq!(
            transcript["steps"][1]["output"], long,
            "no field is truncated"
        );
        assert_eq!(transcript["assistant"], "done");
        assert_eq!(
            projection.coverage,
            serde_json::json!({
                "schema": "powercontext.capture-coverage.v1", "projection": "gateway-turn-text.v2",
                "unit": "gateway_turn_projection", "status": "complete", "reasons": []
            })
        );
    }

    #[test]
    fn an_oversized_turn_is_split_in_order_without_losing_anything() {
        let outputs: Vec<String> = (0..12)
            .map(|i| format!("{i:02}{}", "x".repeat(30_000)))
            .collect();
        let mut request = chat_turn(&outputs);
        // 一个单独就放不下的工具输出，拆成连续的几个步骤。
        request.messages.push(request.messages[1].clone());
        request.messages.push(request.messages[2].clone());
        let huge = "y".repeat(MAX_SOURCE_CHARS * 2);
        request.messages.last_mut().unwrap().content = vec![Value::from(huge.as_str())];
        let projection = turn_transcript(&request, FINAL, "messages").unwrap();
        let count = projection.parts.len();
        assert!(count > 2);
        let mut entries = Vec::new();
        for (index, part) in projection.parts.iter().enumerate() {
            assert!(part.chars().count() <= MAX_SOURCE_CHARS);
            let part: Value = serde_json::from_str(part).unwrap();
            assert_eq!(part["kind"], "gateway_turn");
            assert_eq!(part["part"], index + 1);
            assert_eq!(part["parts"], count);
            entries.extend(part["steps"].as_array().unwrap().clone());
        }
        assert_eq!(
            entries.first().unwrap(),
            &serde_json::json!({"type": "user", "text": "task"})
        );
        assert_eq!(
            entries.last().unwrap(),
            &serde_json::json!({"type": "assistant", "text": "done"})
        );
        let outputs: Vec<&str> = entries
            .iter()
            .filter(|entry| entry["type"] == "tool_output")
            .map(|entry| entry["output"].as_str().unwrap())
            .collect();
        for (i, output) in outputs.iter().take(12).enumerate() {
            assert!(output.starts_with(&format!("{i:02}")));
        }
        assert_eq!(
            outputs[12..].concat(),
            huge,
            "the oversized output is kept whole, in order"
        );
        assert!(entries
            .iter()
            .filter(|entry| entry["type"] == "tool_output")
            .skip(13)
            .all(|entry| entry["continued"] == true));
    }

    #[test]
    fn responses_items_keep_every_field_the_model_read() {
        let mut request = chat_turn(&[]);
        request.messages.extend([
            item(serde_json::json!({"type": "message", "role": "developer",
                "content": [{"type": "input_text", "text": "Run tests before finishing."}]})),
            item(serde_json::json!({"type": "reasoning", "encrypted_content": "gAAAA",
                "summary": [{"type": "summary_text", "text": "Checking the loader."}]})),
            item(serde_json::json!({"type": "apply_patch_call", "id": "ap_1", "call_id": "call_1", "status": "completed",
                "operation": {"type": "update_file", "path": "a.py", "diff": "@@\n-old\n+new"}})),
            item(serde_json::json!({"type": "apply_patch_call_output", "call_id": "call_1", "output": "patched"})),
            item(serde_json::json!({"type": "computer_call_output", "call_id": "call_2",
                "output": {"type": "computer_screenshot", "image_url": "data:image/png;base64,AAAA"}})),
            item(serde_json::json!({"type": "function_call_output", "call_id": "call_3",
                "output": [{"type": "input_image", "image_url": "https://example.com/a.png"}]})),
            item(serde_json::json!({"type": "mcp_approval_response", "approval_request_id": "r1",
                "approve": false, "reason": "too risky"})),
            item(serde_json::json!({"type": "frobnicate", "id": "f_1", "data": "visible payload"})),
        ]);
        let response = serde_json::json!({"output": [
            {"type": "mcp_call", "id": "mcp_1", "name": "search_docs", "server_label": "docs",
             "arguments": "{\"q\":\"loader\"}", "error": "server timed out"},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}
        ]})
        .to_string();
        let projection = turn_transcript(&request, &response, "input").unwrap();
        let transcript = single(&projection);
        assert_eq!(
            transcript["steps"],
            serde_json::json!([
                {"type": "message", "role": "developer", "text": "Run tests before finishing."},
                {"type": "reasoning_summary", "text": "Checking the loader."},
                {"type": "tool_call", "name": "apply_patch_call",
                 "arguments": "{\"diff\":\"@@\\n-old\\n+new\",\"path\":\"a.py\",\"type\":\"update_file\"}"},
                {"type": "tool_output", "output": "patched"},
                {"type": "tool_output",
                 "output": "{\"image_url\":\"[image/png data omitted: 26 bytes]\",\"type\":\"computer_screenshot\"}"},
                {"type": "tool_output", "output": "[input_image: https://example.com/a.png]"},
                {"type": "tool_output", "output": "{\"approval_request_id\":\"r1\",\"approve\":false,\"reason\":\"too risky\"}"},
                {"type": "item", "item_type": "frobnicate", "content": "{\"data\":\"visible payload\"}"},
                {"type": "tool_call", "name": "search_docs",
                 "arguments": "{\"arguments\":\"{\\\"q\\\":\\\"loader\\\"}\",\"server_label\":\"docs\"}"},
                {"type": "tool_output", "output": "server timed out"}
            ])
        );
        assert_eq!(projection.coverage["status"], "complete");
        for absent in ["call_1", "ap_1", "mcp_1", "f_1", "base64", "gAAAA"] {
            assert!(!projection.parts[0].contains(absent), "{absent}");
        }
    }

    #[test]
    fn a_single_argument_field_is_recorded_as_its_text() {
        let mut request = chat_turn(&[]);
        request.messages.extend([
            item(serde_json::json!({"type": "function_call", "call_id": "c", "name": "run", "arguments": "{\"cmd\":\"ls\"}"})),
            item(serde_json::json!({"type": "custom_tool_call", "call_id": "d", "name": "exec", "input": "ls -la"})),
            item(serde_json::json!({"type": "computer_call", "call_id": "e", "action": {"type": "screenshot"}})),
        ]);
        let transcript = single(&turn_transcript(&request, RESPONSES_FINAL, "input").unwrap());
        assert_eq!(
            transcript["steps"],
            serde_json::json!([
                {"type": "tool_call", "name": "run", "arguments": "{\"cmd\":\"ls\"}"},
                {"type": "tool_call", "name": "exec", "arguments": "ls -la"},
                {"type": "tool_call", "name": "computer_call", "arguments": "{\"type\":\"screenshot\"}"}
            ])
        );
    }

    #[test]
    fn chat_calls_of_other_shapes_are_recorded_whole() {
        let mut request = chat_turn(&["done".into()]);
        request.messages[1].tool_calls = Some(vec![
            serde_json::json!({"type": "custom", "id": "x1", "custom": {"name": "exec", "input": "list files"}}),
        ]);
        let transcript = single(&turn_transcript(&request, FINAL, "messages").unwrap());
        assert_eq!(
            transcript["steps"][0],
            serde_json::json!({"type": "tool_call", "name": "custom",
                "arguments": "{\"custom\":{\"input\":\"list files\",\"name\":\"exec\"},\"type\":\"custom\"}"})
        );
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
