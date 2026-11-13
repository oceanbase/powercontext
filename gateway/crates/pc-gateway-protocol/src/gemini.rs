// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::RenderError;
use serde_json::{json, Map, Value};
use std::collections::HashMap;

fn unsupported() -> RenderError {
    RenderError::UnsupportedField {
        renderer: "google-codeassist",
        field: "unsupported Chat-to-Gemini field or content".into(),
    }
}

pub(crate) fn codeassist_request(body: &Value, project: &str) -> Result<Value, RenderError> {
    if body["stream"] == true {
        return Err(unsupported());
    }
    if body.as_object().ok_or_else(unsupported)?.keys().any(|key| {
        !matches!(
            key.as_str(),
            "model"
                | "messages"
                | "stream"
                | "max_tokens"
                | "max_completion_tokens"
                | "tools"
                | "tool_choice"
                | "response_format"
                | "temperature"
                | "top_p"
                | "stop"
                | "seed"
        )
    }) {
        return Err(unsupported());
    }
    let mut contents = Vec::new();
    let mut system = Vec::new();
    let mut names = HashMap::new();
    for message in body["messages"].as_array().ok_or_else(unsupported)? {
        let role = message["role"].as_str().ok_or_else(unsupported)?;
        if message
            .as_object()
            .ok_or_else(unsupported)?
            .keys()
            .any(|k| {
                !matches!(
                    k.as_str(),
                    "role" | "content" | "tool_calls" | "tool_call_id"
                )
            })
        {
            return Err(unsupported());
        }
        let mut parts = Vec::new();
        match &message["content"] {
            Value::String(text) => {
                if !text.is_empty() {
                    parts.push(json!({"text":text}));
                }
            }
            Value::Null if role == "assistant" && message["tool_calls"].is_array() => {}
            Value::Array(items)
                if items
                    .iter()
                    .all(|i| i["type"] == "text" && i["text"].is_string()) =>
            {
                for item in items {
                    parts.push(json!({"text":item["text"]}));
                }
            }
            _ => return Err(unsupported()),
        }
        if role == "tool" {
            let id = message["tool_call_id"].as_str().ok_or_else(unsupported)?;
            let name = names.get(id).ok_or_else(unsupported)?;
            let text = message["content"].as_str().ok_or_else(unsupported)?;
            let response = serde_json::from_str::<Value>(text)
                .ok()
                .filter(Value::is_object)
                .unwrap_or_else(|| json!({"output":text}));
            parts = vec![json!({"functionResponse":{"name":name,"response":response}})];
        }
        if let Some(calls) = message.get("tool_calls") {
            if role != "assistant" {
                return Err(unsupported());
            }
            for call in calls.as_array().ok_or_else(unsupported)? {
                if call["type"] != "function" {
                    return Err(unsupported());
                }
                let id = call["id"].as_str().ok_or_else(unsupported)?;
                let function = &call["function"];
                let name = function["name"].as_str().ok_or_else(unsupported)?;
                let args: Value =
                    serde_json::from_str(function["arguments"].as_str().ok_or_else(unsupported)?)
                        .map_err(|_| unsupported())?;
                if !args.is_object() {
                    return Err(unsupported());
                }
                names.insert(id.to_owned(), name.to_owned());
                let mut part = json!({"functionCall":{"name":name,"args":args}});
                if let Some(signature) = call.pointer("/extra_content/google/thought_signature") {
                    part["thoughtSignature"] = signature.clone();
                }
                parts.push(part);
            }
        }
        match role {
            "system" | "developer" => system.extend(parts),
            "user" | "tool" => contents.push(json!({"role":"user","parts":parts})),
            "assistant" => contents.push(json!({"role":"model","parts":parts})),
            _ => return Err(unsupported()),
        }
    }
    let mut request = json!({"contents":contents});
    if !system.is_empty() {
        request["systemInstruction"] = json!({"parts":system});
    }
    let mut generation = Map::new();
    for (source, target) in [
        ("max_tokens", "maxOutputTokens"),
        ("max_completion_tokens", "maxOutputTokens"),
        ("temperature", "temperature"),
        ("top_p", "topP"),
        ("seed", "seed"),
    ] {
        if let Some(value) = body.get(source) {
            generation.insert(target.into(), value.clone());
        }
    }
    if let Some(stop) = body.get("stop") {
        generation.insert(
            "stopSequences".into(),
            if stop.is_string() {
                json!([stop])
            } else {
                stop.clone()
            },
        );
    }
    if let Some(format) = body.get("response_format") {
        match format["type"].as_str() {
            Some("text") => {}
            Some("json_object") => {
                generation.insert("responseMimeType".into(), json!("application/json"));
            }
            Some("json_schema") => {
                let schema = format
                    .pointer("/json_schema/schema")
                    .filter(|s| s.is_object())
                    .ok_or_else(unsupported)?;
                generation.insert("responseMimeType".into(), json!("application/json"));
                generation.insert("responseJsonSchema".into(), schema.clone());
            }
            _ => return Err(unsupported()),
        }
    }
    if !generation.is_empty() {
        request["generationConfig"] = Value::Object(generation);
    }
    if let Some(tools) = body.get("tools") {
        let mut declarations = Vec::new();
        for tool in tools.as_array().ok_or_else(unsupported)? {
            if tool["type"] != "function" || tool["function"]["strict"] == true {
                return Err(unsupported());
            }
            let function = tool["function"].as_object().ok_or_else(unsupported)?;
            if function
                .keys()
                .any(|k| !matches!(k.as_str(), "name" | "description" | "parameters" | "strict"))
            {
                return Err(unsupported());
            }
            let mut declaration = Value::Object(function.clone());
            declaration.as_object_mut().unwrap().remove("strict");
            declarations.push(declaration);
        }
        request["tools"] = json!([{"functionDeclarations":declarations}]);
    }
    if let Some(choice) = body.get("tool_choice") {
        let config = match choice.as_str() {
            Some("auto") => json!({"mode":"AUTO"}),
            Some("none") => json!({"mode":"NONE"}),
            Some("required") => json!({"mode":"ANY"}),
            _ if choice["type"] == "function" && choice["function"]["name"].is_string() => {
                json!({"mode":"ANY","allowedFunctionNames":[choice["function"]["name"]]})
            }
            _ => return Err(unsupported()),
        };
        request["toolConfig"] = json!({"functionCallingConfig":config});
    }
    Ok(json!({"model":body["model"],"project":project,"request":request}))
}

/// Convert before release admission. Unknown content is not silently hidden behind success.
pub fn codeassist_chat_response(bytes: &[u8], model: &str) -> Option<Vec<u8>> {
    let wrapped = crate::json::unambiguous_value(bytes)?;
    let response = wrapped.get("response")?.as_object()?;
    let candidates = response.get("candidates")?.as_array()?;
    if candidates.is_empty() {
        return None;
    }
    let id = response
        .get("responseId")
        .and_then(Value::as_str)
        .map(String::from)
        .unwrap_or_else(|| format!("gemini-{}", uuid::Uuid::new_v4()));
    let mut choices = Vec::new();
    for (index, candidate) in candidates.iter().enumerate() {
        let finish = match candidate["finishReason"].as_str()? {
            "STOP" => "stop",
            "MAX_TOKENS" => "length",
            "SAFETY" | "RECITATION" | "BLOCKLIST" | "PROHIBITED_CONTENT" | "SPII" => {
                "content_filter"
            }
            _ => return None,
        };
        let parts = candidate
            .pointer("/content/parts")
            .and_then(Value::as_array);
        let mut text = String::new();
        let mut reasoning = String::new();
        let mut calls = Vec::new();
        for part in parts.into_iter().flatten() {
            let object = part.as_object()?;
            if object
                .keys()
                .any(|key| !matches!(key.as_str(), "text" | "thought" | "functionCall"))
            {
                return None;
            }
            if part["thought"] == true {
                reasoning.push_str(part["text"].as_str()?);
                continue;
            }
            if let Some(content) = part["text"].as_str() {
                text.push_str(content);
            } else {
                let function = part.get("functionCall")?;
                let args = function.get("args").filter(|v| v.is_object())?;
                let name = function.get("name")?.as_str()?;
                let call = json!({"type":"function","id":format!("{id}-call-{}",calls.len()),
                    "function":{"name":name,"arguments":serde_json::to_string(args).ok()?}});
                calls.push(call);
            }
        }
        let mut message = json!({"role":"assistant","content":text});
        if !reasoning.is_empty() {
            message["reasoning_content"] = reasoning.into();
        }
        if !calls.is_empty() {
            message["tool_calls"] = json!(calls);
        }
        choices.push(json!({"index":index,"message":message,"finish_reason":if calls.is_empty(){finish}else{"tool_calls"}}));
    }
    let created = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs();
    let mut output = json!({"id":id,"object":"chat.completion","created":created,"model":model,"choices":choices});
    if let Some(usage) = response.get("usageMetadata").and_then(Value::as_object) {
        let mut normalized = Map::new();
        for (source, target) in [
            ("promptTokenCount", "prompt_tokens"),
            ("candidatesTokenCount", "completion_tokens"),
            ("totalTokenCount", "total_tokens"),
        ] {
            if let Some(value) = usage.get(source) {
                normalized.insert(target.into(), value.clone());
            }
        }
        if let Some(cached) = usage.get("cachedContentTokenCount") {
            normalized.insert(
                "prompt_tokens_details".into(),
                json!({"cached_tokens":cached}),
            );
        }
        if let Some(thoughts) = usage.get("thoughtsTokenCount") {
            let completion = usage
                .get("candidatesTokenCount")?
                .as_u64()?
                .checked_add(thoughts.as_u64()?)?;
            normalized.insert("completion_tokens".into(), Value::from(completion));
        }
        output["usage"] = Value::Object(normalized);
    }
    serde_json::to_vec(&output).ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn goose_shared_envelope_vector_and_text_expectation() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../tests/fixtures/oauth/upstream/provider-vectors.json");
        let fixture: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        let vector = &fixture["gemini"];
        let body = json!({"model":"gemini-2.5-pro","messages":[
            {"role":"system","content":"be helpful"},{"role":"user","content":"hello"}],"stream":false});
        let request = codeassist_request(&body, "project-123").unwrap();
        assert_eq!(request, vector["expected_request"]);
        // The upstream fixture is an SSE chunk without a terminal finish reason. It cannot
        // authorize a complete response in this non-streaming adapter.
        assert!(codeassist_chat_response(
            &serde_json::to_vec(&vector["response"]).unwrap(),
            "gemini-2.5-pro"
        )
        .is_none());
        let mut response = vector["response"].clone();
        response["response"]["candidates"][0]["finishReason"] = json!("STOP");
        let bytes =
            codeassist_chat_response(&serde_json::to_vec(&response).unwrap(), "gemini-2.5-pro")
                .unwrap();
        let chat: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(
            chat["choices"][0]["message"]["content"],
            vector["expected_response"]["candidates"][0]["content"]["parts"][0]["text"]
        );
        assert_eq!(
            chat["usage"]["prompt_tokens"],
            vector["expected_response"]["usageMetadata"]["promptTokenCount"]
        );
    }
}
