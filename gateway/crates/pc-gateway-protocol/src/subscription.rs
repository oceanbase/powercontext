// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0
// Grok shaping derives from Magpie (MIT); see LICENSES/oauth.md.

use crate::{CanonicalRequest, GatewayRenderer, RenderError, RenderedPayload, WireRenderer};
use serde_json::{json, Value};

#[derive(Clone)]
pub enum SubscriptionProtocol {
    Grok,
    Codex,
    Kimi,
    Gemini { project: String },
    Claude,
}

/// Provider adaptation occurs on every render, including prepare refresh and degraded rendering.
/// The executor therefore checks exactly the bytes that the HTTP transport sends.
pub struct SubscriptionRenderer {
    pub inner: GatewayRenderer,
    pub subscription: Option<SubscriptionProtocol>,
}

impl WireRenderer for SubscriptionRenderer {
    fn name(&self) -> &'static str {
        self.inner.name()
    }

    fn message_list_key(&self) -> &'static str {
        self.inner.message_list_key()
    }

    fn render(
        &self,
        request: &CanonicalRequest,
        plan: Option<&Value>,
    ) -> Result<RenderedPayload, RenderError> {
        let payload = self.inner.render(request, plan)?;
        let Some(ref subscription) = self.subscription else {
            return Ok(payload);
        };
        let mut body: Value = serde_json::from_slice(&payload.bytes).map_err(|_| invalid())?;
        match subscription {
            SubscriptionProtocol::Codex => {
                if self.message_list_key() != "input" || body.get("max_output_tokens").is_some() {
                    return Err(RenderError::UnsupportedField {
                        renderer: "codex-subscription",
                        field: "requires Responses without max_output_tokens".into(),
                    });
                }
                body["stream"] = json!(true);
                body["store"] = json!(false);
                body.as_object_mut()
                    .ok_or_else(invalid)?
                    .entry("instructions")
                    .or_insert_with(|| json!("You are Codex."));
            }
            SubscriptionProtocol::Grok => {
                if self.message_list_key() == "input" {
                    grok_body(&mut body)?;
                }
            }
            SubscriptionProtocol::Kimi => {
                if self.message_list_key() == "input" {
                    body["stream"] = json!(true);
                    body["store"] = json!(false);
                }
            }
            SubscriptionProtocol::Gemini { project } => {
                if self.message_list_key() != "messages" {
                    return Err(invalid());
                }
                body = crate::gemini::codeassist_request(&body, project)?;
            }
            SubscriptionProtocol::Claude => {
                if self.message_list_key() != "messages"
                    || body["stream"] == true
                    || body
                        .as_object()
                        .ok_or_else(invalid)?
                        .keys()
                        .any(|k| !matches!(k.as_str(), "model" | "messages" | "stream"))
                    || body["messages"].as_array().is_none_or(|messages| {
                        messages.iter().any(|m| {
                            !m["content"].is_string()
                                || !matches!(
                                    m["role"].as_str(),
                                    Some("system" | "developer" | "user" | "assistant")
                                )
                                || m.as_object().is_none_or(|m| {
                                    m.keys().any(|k| !matches!(k.as_str(), "role" | "content"))
                                })
                        })
                    })
                {
                    return Err(RenderError::UnsupportedField {
                        renderer: "claude-cli",
                        field: "text-only single-turn CLI input".into(),
                    });
                }
            }
        }
        Ok(RenderedPayload {
            bytes: serde_json::to_vec(&body).map_err(|_| invalid())?.into(),
            content_type: payload.content_type,
        })
    }
}

fn invalid() -> RenderError {
    RenderError::InvalidRenderPlan {
        renderer: "subscription",
    }
}

fn supported_tool(value: &Value) -> bool {
    matches!(
        value.as_str(),
        Some(
            "function"
                | "web_search"
                | "x_search"
                | "image_generation"
                | "collections_search"
                | "file_search"
                | "code_execution"
                | "code_interpreter"
                | "mcp"
                | "shell"
                | "tool_search"
        )
    )
}

fn flat_name(namespace: &str, name: &str) -> Result<String, RenderError> {
    let name = if namespace.is_empty() || namespace == "functions" {
        name.into()
    } else {
        format!("{namespace}__{name}")
    };
    // Reject rather than silently alias long names. Hash truncation is not part of this profile.
    if name.len() > 64 {
        return Err(RenderError::UnsupportedField {
            renderer: "grok-subscription",
            field: "tool name length".into(),
        });
    }
    Ok(name)
}

fn flat_call(value: &mut Value) -> Result<(), RenderError> {
    if !matches!(value["type"].as_str(), Some("function" | "function_call")) {
        return Ok(());
    }
    if let (Some(namespace), Some(name)) = (value["namespace"].as_str(), value["name"].as_str()) {
        let name = flat_name(namespace, name)?;
        value["name"] = json!(name);
        value
            .as_object_mut()
            .ok_or_else(invalid)?
            .remove("namespace");
    }
    Ok(())
}

fn grok_body(body: &mut Value) -> Result<(), RenderError> {
    if let Some(tools) = body.get_mut("tools").and_then(Value::as_array_mut) {
        let mut kept = Vec::new();
        for tool in tools.drain(..) {
            if tool["type"] == "namespace" {
                let namespace = tool["name"].as_str().unwrap_or("");
                if let Some(nested) = tool["tools"].as_array() {
                    for function in nested.iter().filter(|t| t["type"] == "function") {
                        if let Some(name) = function["name"].as_str().filter(|v| !v.is_empty()) {
                            let mut function = function.clone();
                            function["name"] = json!(flat_name(namespace, name)?);
                            kept.push(function);
                        }
                    }
                }
            } else if supported_tool(&tool["type"]) {
                kept.push(tool);
            }
        }
        for tool in &mut kept {
            if let Some(object) = tool.as_object_mut() {
                object.remove("external_web_access");
            }
            if let Some(parameters) = tool
                .get("parameters")
                .filter(|_| tool["type"] == "function")
            {
                if parameters.get("type").is_some_and(|v| v != "object")
                    || ["anyOf", "allOf", "oneOf"]
                        .iter()
                        .any(|key| parameters.get(key).is_some())
                {
                    return Err(RenderError::UnsupportedField {
                        renderer: "grok-subscription",
                        field: "non-object tool schema root".into(),
                    });
                }
            }
        }
        *tools = kept;
    }
    let has_tools = body["tools"].as_array().is_some_and(|v| !v.is_empty());
    if !has_tools
        || body
            .get("tool_choice")
            .filter(|v| v.is_object())
            .is_some_and(|v| !supported_tool(&v["type"]))
    {
        body.as_object_mut()
            .ok_or_else(invalid)?
            .remove("tool_choice");
    } else if let Some(choice) = body.get_mut("tool_choice") {
        flat_call(choice)?;
    }
    if let Some(input) = body.get_mut("input").and_then(Value::as_array_mut) {
        for item in input {
            flat_call(item)?;
            if item["type"] == "reasoning" && item.get("content").is_some_and(Value::is_null) {
                item.as_object_mut().ok_or_else(invalid)?.remove("content");
            }
            if item["type"] == "agent_message" {
                // Sealed or unfamiliar items remain available for Core to withhold explicitly.
                if item.get("encrypted_content").is_some() {
                    continue;
                }
                let Some(parts) = item["content"].as_array().filter(|parts| {
                    !parts.is_empty()
                        && parts.iter().all(|p| {
                            p.get("encrypted_content").is_none()
                                && ((p["type"] == "input_text" && p["text"].is_string())
                                    || (p["type"] == "input_image" && p["image_url"].is_string()))
                        })
                }) else {
                    continue;
                };
                let mut content = parts.clone();
                let from = item["author"].as_str().unwrap_or("");
                let to = item["recipient"].as_str().unwrap_or("");
                let header = match (from.is_empty(), to.is_empty()) {
                    (false, false) => format!("From {from} to {to}"),
                    (false, true) => format!("From {from}"),
                    (true, false) => format!("To {to}"),
                    (true, true) => String::new(),
                };
                if !header.is_empty() {
                    content.insert(
                        0,
                        json!({"type":"input_text", "text": format!("{header}\n\n")}),
                    );
                }
                *item = json!({"type":"message", "role":"user", "content":content});
            }
        }
    }
    Ok(())
}

/// A Codex subscription always streams. Only a complete terminal Response is a releasable unit.
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

#[cfg(test)]
mod migrated_vectors {
    #[test]
    fn magpie_grok_original_inputs_and_independent_expectations() {
        let fixture: serde_json::Value = serde_json::from_str(include_str!(
            "../tests/fixtures/subscriptions/magpie-grok.json"
        ))
        .unwrap();
        for case in fixture["cases"].as_array().unwrap() {
            let mut body = case["request"].clone();
            super::grok_body(&mut body).unwrap();
            assert_eq!(
                body, case["expected"],
                "{} / {}",
                case["id"], case["source_test"]
            );
        }
    }
}
