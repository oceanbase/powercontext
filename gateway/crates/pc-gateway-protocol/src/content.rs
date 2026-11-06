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

//! Locate model output in an already buffered wire response, without retaining its content.
use serde::Deserialize;
use serde_json::value::RawValue;
use std::borrow::Cow;

#[derive(Debug, Clone, Copy)]
pub enum OutputProtocol {
    OpenAiChat,
    OpenAiResponses,
}

/// Byte offset of the first encoded byte in a nonempty supported model-output string.
/// Only the offset survives inspection; callers must observe the corresponding outgoing body frame.
/// Invalid/unsupported output and responses without recognized content return None.
pub fn first_model_content_offset(
    body: &[u8],
    protocol: OutputProtocol,
    sse: bool,
) -> Option<usize> {
    let text = std::str::from_utf8(body).ok()?;
    if sse {
        sse_offset(text, protocol)
    } else {
        json_offset(text, protocol, false)
    }
}

// Borrow raw field slices instead of allocating copies of model text or decoded JSON trees.
#[derive(Default, Deserialize)]
struct Fields<'a> {
    #[serde(borrow, rename = "type")]
    kind: Option<Cow<'a, str>>,
    #[serde(borrow)]
    role: Option<Cow<'a, str>>,
    #[serde(borrow)]
    choices: Option<&'a RawValue>,
    #[serde(borrow)]
    message: Option<&'a RawValue>,
    #[serde(borrow)]
    delta: Option<&'a RawValue>,
    #[serde(borrow)]
    content: Option<&'a RawValue>,
    #[serde(borrow)]
    refusal: Option<&'a RawValue>,
    #[serde(borrow)]
    tool_calls: Option<&'a RawValue>,
    #[serde(borrow)]
    function: Option<&'a RawValue>,
    #[serde(borrow)]
    function_call: Option<&'a RawValue>,
    #[serde(borrow)]
    name: Option<&'a RawValue>,
    #[serde(borrow)]
    arguments: Option<&'a RawValue>,
    #[serde(borrow)]
    output: Option<&'a RawValue>,
    #[serde(borrow)]
    text: Option<&'a RawValue>,
    #[serde(borrow)]
    item: Option<&'a RawValue>,
    #[serde(borrow)]
    part: Option<&'a RawValue>,
    #[serde(borrow)]
    response: Option<&'a RawValue>,
}

fn fields(raw: Option<&RawValue>) -> Fields<'_> {
    raw.and_then(|r| serde_json::from_str(r.get()).ok())
        .unwrap_or_default()
}

fn array(raw: Option<&RawValue>) -> Vec<&RawValue> {
    raw.and_then(|r| serde_json::from_str(r.get()).ok())
        .unwrap_or_default()
}

fn string_offset(raw: Option<&RawValue>, text: &str) -> Option<usize> {
    let value = raw?.get();
    // RawValue has already validated JSON. Only "" is an empty JSON string, including escapes.
    if !value.starts_with('"') || value.len() <= 2 {
        return None;
    }
    (value.as_ptr() as usize)
        .checked_sub(text.as_ptr() as usize)?
        .checked_add(1)
        .filter(|offset| *offset < text.len())
}

fn function_offset(function: Fields<'_>, text: &str) -> Option<usize> {
    [
        string_offset(function.name, text),
        string_offset(function.arguments, text),
    ]
    .into_iter()
    .flatten()
    .min()
}

fn part_offset(raw: Option<&RawValue>, text: &str) -> Option<usize> {
    let part = fields(raw);
    match part.kind.as_deref() {
        Some("output_text" | "text") => string_offset(part.text, text),
        Some("refusal") => string_offset(part.refusal, text),
        _ => None,
    }
}

fn message_offset(message: Fields<'_>, text: &str) -> Option<usize> {
    if message
        .role
        .as_deref()
        .is_some_and(|role| role != "assistant")
    {
        return None;
    }
    let content = string_offset(message.content, text).into_iter().chain(
        array(message.content)
            .into_iter()
            .filter_map(|part| part_offset(Some(part), text)),
    );
    content
        .chain(string_offset(message.refusal, text))
        .chain(function_offset(fields(message.function_call), text))
        .chain(
            array(message.tool_calls)
                .into_iter()
                .filter_map(|tool| function_offset(fields(fields(Some(tool)).function), text)),
        )
        .min()
}

fn item_offset(raw: Option<&RawValue>, text: &str) -> Option<usize> {
    let item = fields(raw);
    match item.kind.as_deref() {
        Some("message") if item.role.as_deref().is_none_or(|role| role == "assistant") => {
            array(item.content)
                .into_iter()
                .filter_map(|part| part_offset(Some(part), text))
                .min()
        }
        Some("function_call") => function_offset(item, text),
        _ => None,
    }
}

fn responses_offset(response: Fields<'_>, text: &str) -> Option<usize> {
    array(response.output)
        .into_iter()
        .filter_map(|item| item_offset(Some(item), text))
        .min()
}

fn json_offset(text: &str, protocol: OutputProtocol, streaming: bool) -> Option<usize> {
    let root: Fields<'_> = serde_json::from_str(text).ok()?;
    match protocol {
        OutputProtocol::OpenAiChat => array(root.choices)
            .into_iter()
            .filter_map(|choice| {
                let choice = fields(Some(choice));
                message_offset(
                    fields(if streaming {
                        choice.delta
                    } else {
                        choice.message
                    }),
                    text,
                )
            })
            .min(),
        OutputProtocol::OpenAiResponses => responses_offset(root, text),
    }
}

fn response_event_offset(text: &str, event: Option<&str>) -> Option<usize> {
    let root: Fields<'_> = serde_json::from_str(text).ok()?;
    if event
        .zip(root.kind.as_deref())
        .is_some_and(|(event, kind)| event != kind)
    {
        return None;
    }
    match root.kind.as_deref().or(event)? {
        "response.output_text.delta"
        | "response.refusal.delta"
        | "response.function_call_arguments.delta" => string_offset(root.delta, text),
        "response.output_text.done" => string_offset(root.text, text),
        "response.refusal.done" => string_offset(root.refusal, text),
        "response.function_call_arguments.done" => string_offset(root.arguments, text),
        "response.content_part.added" | "response.content_part.done" => {
            part_offset(root.part, text)
        }
        "response.output_item.added" | "response.output_item.done" => item_offset(root.item, text),
        "response.completed" | "response.incomplete" => {
            responses_offset(fields(root.response), text)
        }
        // created, role, usage, reasoning and error/control-only events do not establish model content.
        _ => None,
    }
}

fn sse_offset(text: &str, protocol: OutputProtocol) -> Option<usize> {
    let mut data = String::new();
    let mut segments = Vec::new();
    let mut event = None;
    let mut original = 0;
    // LF and CRLF framing, matching the supported SSE wire profiles. Unfinished events are not dispatched.
    for line in text.split_inclusive('\n') {
        let line_start = original;
        original += line.len();
        let Some(line) = line.strip_suffix('\n') else {
            break;
        };
        let line = line.strip_suffix('\r').unwrap_or(line);
        if line.is_empty() {
            let offset = match protocol {
                OutputProtocol::OpenAiChat => json_offset(&data, protocol, true),
                OutputProtocol::OpenAiResponses => response_event_offset(&data, event),
            };
            if let Some(offset) = offset {
                return segments.iter().find_map(|&(joined, start, len)| {
                    (offset >= joined && offset < joined + len).then(|| start + offset - joined)
                });
            }
            data.clear();
            segments.clear();
            event = None;
        } else if let Some(value) = line.strip_prefix("data:") {
            let value = value.strip_prefix(' ').unwrap_or(value);
            if !segments.is_empty() {
                data.push('\n');
            }
            let start = line_start + line.len() - value.len();
            segments.push((data.len(), start, value.len()));
            data.push_str(value);
        } else if let Some(value) = line.strip_prefix("event:") {
            event = Some(value.strip_prefix(' ').unwrap_or(value));
        }
    }
    None
}
