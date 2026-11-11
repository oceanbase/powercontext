// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

// Grok shaping derives from Magpie (MIT); see LICENSES/oauth.md.

use crate::{CanonicalRequest, RenderError};
use bytes::Bytes;
use serde_json::{json, Value};
use std::collections::BTreeMap;

#[derive(PartialEq, Eq)]
struct ToolIdentity {
    namespace: String,
    name: String,
}

// Magpie's namespacedIn/callTo retain identities per request, including history.
fn tool_names<'a>(
    tools: impl Iterator<Item = &'a Value>,
    input: impl Iterator<Item = &'a Value>,
    choice: Option<&'a Value>,
) -> Result<BTreeMap<String, ToolIdentity>, RenderError> {
    let mut names = BTreeMap::new();
    let mut remember = |namespace: &str, name: &str| -> Result<(), RenderError> {
        let flat = flat_name(namespace, name)?;
        let identity = ToolIdentity {
            namespace: if namespace == "functions" {
                ""
            } else {
                namespace
            }
            .into(),
            name: name.into(),
        };
        if names
            .get(&flat)
            .is_some_and(|previous| previous != &identity)
        {
            return Err(unsupported("ambiguous flattened tool name"));
        }
        names.insert(flat, identity);
        Ok(())
    };
    for tool in tools {
        if tool["type"] == "namespace" {
            for function in tool["tools"].as_array().into_iter().flatten() {
                if function["type"] == "function" {
                    if let Some(name) = function["name"].as_str() {
                        remember(tool["name"].as_str().unwrap_or(""), name)?;
                    }
                }
            }
        } else if tool["type"] == "function" {
            if let Some(name) = tool["name"].as_str() {
                remember("", name)?;
            }
        }
    }
    let selectors = choice
        .filter(|choice| choice["type"] == "allowed_tools")
        .and_then(|choice| choice["tools"].as_array())
        .into_iter()
        .flatten();
    for item in input.chain(choice).chain(selectors) {
        if matches!(item["type"].as_str(), Some("function" | "function_call")) {
            if let Some(name) = item["name"].as_str() {
                remember(item["namespace"].as_str().unwrap_or(""), name)?;
            }
        }
    }
    Ok(names)
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

pub(super) fn adapt(body: &mut Value) -> Result<(), RenderError> {
    tool_names(
        body["tools"].as_array().into_iter().flatten(),
        body["input"].as_array().into_iter().flatten(),
        body.get("tool_choice"),
    )?;
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
    if body["tool_choice"]["type"] == "allowed_tools" {
        // Magpie's parseResponses filters declarations and retains the requested mode.
        let mode = body["tool_choice"]["mode"]
            .as_str()
            .filter(|mode| matches!(*mode, "auto" | "required"))
            .ok_or_else(|| unsupported("allowed_tools mode"))?
            .to_owned();
        let mut allowed = body["tool_choice"]["tools"]
            .as_array()
            .ok_or_else(|| unsupported("allowed_tools tools"))?
            .clone();
        for selector in &mut allowed {
            if !supported_tool(&selector["type"])
                || selector["type"] == "function" && !selector["name"].is_string()
            {
                return Err(unsupported("allowed_tools selector"));
            }
            flat_call(selector)?;
        }
        if let Some(tools) = body.get_mut("tools").and_then(Value::as_array_mut) {
            tools.retain(|tool| {
                allowed.iter().any(|selector| {
                    selector.as_object().is_some_and(|fields| {
                        fields
                            .iter()
                            .all(|(key, value)| tool.get(key) == Some(value))
                    })
                })
            });
        }
        body["tool_choice"] = json!(mode);
    }
    let has_tools = body["tools"].as_array().is_some_and(|v| !v.is_empty());
    if let Some(choice) = body.get_mut("tool_choice") {
        if choice.is_object() && !supported_tool(&choice["type"]) {
            return Err(unsupported("tool_choice"));
        }
        flat_call(choice)?;
        if !has_tools {
            if !choice.is_null() && !matches!(choice.as_str(), Some("auto" | "none")) {
                return Err(unsupported("tool_choice requires supported tools"));
            }
            body.as_object_mut()
                .ok_or_else(invalid)?
                .remove("tool_choice");
        }
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

fn invalid() -> RenderError {
    RenderError::InvalidRenderPlan {
        renderer: "subscription",
    }
}

fn unsupported(field: &str) -> RenderError {
    RenderError::UnsupportedField {
        renderer: "grok-subscription",
        field: field.into(),
    }
}

pub(super) fn restore_response(request: &CanonicalRequest, body: Bytes) -> Option<Bytes> {
    let names = tool_names(
        request.tools.iter(),
        request
            .messages
            .iter()
            .flat_map(|message| message.content.iter()),
        request.tool_choice.as_ref(),
    )
    .ok()?;
    if names.values().all(|identity| identity.namespace.is_empty()) {
        return Some(body);
    }
    let mut response = crate::json::unambiguous_value(&body)?;
    let mut changed = false;
    if let Some(items) = response.get_mut("output").and_then(Value::as_array_mut) {
        for item in items {
            if item["type"] != "function_call" {
                continue;
            }
            let identity =
                if let Some(namespace) = item["namespace"].as_str().filter(|ns| !ns.is_empty()) {
                    names.values().find(|identity| {
                        identity.namespace == namespace && item["name"] == identity.name
                    })
                } else {
                    item["name"].as_str().and_then(|name| names.get(name))
                };
            let Some(identity) = identity.filter(|identity| !identity.namespace.is_empty()) else {
                continue;
            };
            item["name"] = json!(identity.name);
            item["namespace"] = json!(identity.namespace);
            // Magpie marks unsealed arguments explicitly for Codex sub-agent dispatch.
            item.as_object_mut()?
                .entry("encrypted_function_args")
                .or_insert_with(|| json!([]));
            changed = true;
        }
    }
    if changed {
        serde_json::to_vec(&response).ok().map(Bytes::from)
    } else {
        Some(body)
    }
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
            // The upstream drops an explicitly selected custom tool; reject that loss of intent.
            if case["id"] == "unsupported-tools" {
                assert!(
                    matches!(super::adapt(&mut body), Err(crate::RenderError::UnsupportedField {field, ..}) if field == "tool_choice")
                );
                continue;
            }
            super::adapt(&mut body).unwrap();
            assert_eq!(
                body, case["expected"],
                "{} / {}",
                case["id"], case["source_test"]
            );
        }
    }
}
