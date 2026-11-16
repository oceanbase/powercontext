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
