// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::RenderError;
use serde_json::{json, Value};

pub(super) fn adapt(body: &mut Value, message_list_key: &str) -> Result<(), RenderError> {
    if message_list_key != "input" || body.get("max_output_tokens").is_some() {
        return Err(RenderError::UnsupportedField {
            renderer: "codex-subscription",
            field: "requires Responses without max_output_tokens".into(),
        });
    }
    body["stream"] = json!(true);
    body["store"] = json!(false);
    body.as_object_mut()
        .ok_or(RenderError::InvalidRenderPlan {
            renderer: "subscription",
        })?
        .entry("instructions")
        .or_insert_with(|| json!("You are Codex."));
    Ok(())
}
