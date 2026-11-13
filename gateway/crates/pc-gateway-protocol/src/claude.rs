// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::RenderError;
use serde_json::Value;

pub(super) fn validate(body: &Value, message_list_key: &str) -> Result<(), RenderError> {
    if message_list_key != "messages"
        || body["stream"] == true
        || body
            .as_object()
            .ok_or(RenderError::InvalidRenderPlan {
                renderer: "subscription",
            })?
            .keys()
            .any(|k| !matches!(k.as_str(), "model" | "messages" | "stream"))
        || body["messages"].as_array().is_none_or(|messages| {
            messages.iter().any(|m| {
                !m["content"].is_string()
                    || !matches!(
                        m["role"].as_str(),
                        Some("system" | "developer" | "user" | "assistant")
                    )
                    || m.as_object()
                        .is_none_or(|m| m.keys().any(|k| !matches!(k.as_str(), "role" | "content")))
            })
        })
    {
        return Err(RenderError::UnsupportedField {
            renderer: "claude-cli",
            field: "text-only single-turn CLI input".into(),
        });
    }
    Ok(())
}
