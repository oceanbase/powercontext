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
