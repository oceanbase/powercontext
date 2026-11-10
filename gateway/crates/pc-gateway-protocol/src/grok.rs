// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

// Grok shaping derives from Magpie (MIT); see LICENSES/oauth.md.

use crate::RenderError;
use serde_json::{json, Value};

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

fn invalid() -> RenderError {
    RenderError::InvalidRenderPlan {
        renderer: "subscription",
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
            super::adapt(&mut body).unwrap();
            assert_eq!(
                body, case["expected"],
                "{} / {}",
                case["id"], case["source_test"]
            );
        }
    }
}
