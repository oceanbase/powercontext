// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use serde_json::{json, Value};

/// Codex and Kimi share this Responses wire contract: only a complete terminal unit is releasable.
/// The returned JSON must go through before_release, never straight to the client.
pub fn buffered_subscription_response(bytes: &[u8]) -> Option<Vec<u8>> {
    std::str::from_utf8(bytes).ok()?;
    let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes);
    let mut framer = crate::SseFramer::default();
    framer.push(bytes);
    let mut events = framer.take_events();
    events.extend(framer.finish());
    let mut terminal = None;
    let mut items = std::collections::BTreeMap::new();
    let mut text =
        std::collections::BTreeMap::<usize, std::collections::BTreeMap<usize, String>>::new();
    let mut tool_delta_indices = std::collections::BTreeSet::new();
    for event in events {
        if event.data == "[DONE]" {
            continue;
        }
        let value: Value = serde_json::from_slice(&event.data).ok()?;
        if terminal.is_some() {
            return None;
        }
        match value["type"].as_str() {
            Some("response.output_item.done") => {
                let item = value.get("item").filter(|v| v.is_object())?.clone();
                let index = match value.get("output_index") {
                    Some(index) => usize::try_from(index.as_u64()?).ok()?,
                    None => items.len(),
                };
                if index > 4096 || items.insert(index, item).is_some() {
                    return None;
                }
            }
            Some("response.output_text.delta") => {
                let index = value
                    .get("output_index")
                    .map(Value::as_u64)
                    .unwrap_or(Some(0))?;
                let part = value
                    .get("content_index")
                    .map(Value::as_u64)
                    .unwrap_or(Some(0))?;
                if index > 4096 || part > 4096 {
                    return None;
                }
                text.entry(index as usize)
                    .or_default()
                    .entry(part as usize)
                    .or_default()
                    .push_str(value.get("delta")?.as_str()?);
            }
            Some(
                "response.function_call_arguments.delta" | "response.custom_tool_call_input.delta",
            ) => {
                let index = usize::try_from(value.get("output_index")?.as_u64()?).ok()?;
                tool_delta_indices.insert(index);
            }
            Some("response.completed" | "response.incomplete" | "response.failed") => {
                let mut response = value.get("response")?.clone();
                if terminal.is_some() || !response.is_object() || !response["output"].is_array() {
                    return None;
                }
                if response["output"].as_array()?.is_empty() {
                    for (index, parts) in &text {
                        if !items.contains_key(index) {
                            let content: Vec<Value> = parts.iter().enumerate().map(|(expected, (part,text))| {
                                (expected == *part).then(|| json!({"type":"output_text","text":text,"annotations":[]}))
                            }).collect::<Option<_>>()?;
                            items.insert(*index,json!({"id":format!("msg_{index}"),"type":"message","role":"assistant",
                                "status":"completed","content":content}));
                        }
                    }
                    // Argument deltas require their final item, not merely an empty terminal.
                    if tool_delta_indices.iter().any(|index| {
                        !items.get(index).is_some_and(|v| {
                            matches!(
                                v["type"].as_str(),
                                Some("function_call" | "custom_tool_call")
                            )
                        })
                    }) {
                        return None;
                    }
                    let output: Vec<Value> = std::mem::take(&mut items)
                        .into_iter()
                        .enumerate()
                        .map(|(expected, (index, item))| (expected == index).then_some(item))
                        .collect::<Option<_>>()?;
                    response["output"] = json!(output);
                }
                terminal = Some(serde_json::to_vec(&response).ok()?);
            }
            Some("error") => return None,
            _ => {}
        }
    }
    terminal
}
