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

use pc_gateway_protocol::{CanonicalRequest, Role};
use serde_json::Value;

/// The current prompt is the latest user message other than a standalone harness reminder.
pub(crate) fn last_user_index(request: &CanonicalRequest) -> Option<usize> {
    request
        .messages
        .iter()
        .rposition(|message| message.role == Role::User && !is_reminder_only(&message.content))
}

pub(crate) fn is_text_part(part: &Value) -> bool {
    matches!(
        part.get("type").and_then(Value::as_str),
        Some("text" | "input_text")
    )
}

pub(crate) fn text_part(part: &Value) -> Option<&str> {
    if is_text_part(part) {
        part.get("text").and_then(Value::as_str)
    } else {
        None
    }
}

fn is_reminder_only(content: &[Value]) -> bool {
    match content {
        [Value::String(text)] => is_reminder_text(text),
        [part] => text_part(part).is_some_and(is_reminder_text),
        parts => parts
            .iter()
            .map(text_part)
            .collect::<Option<Vec<_>>>()
            .is_some_and(|texts| is_reminder_text(&texts.join("\n"))),
    }
}

fn is_reminder_text(text: &str) -> bool {
    let mut remaining = text.trim();
    while let Some(block) = remaining.strip_prefix("<system-reminder>") {
        let Some((_, rest)) = block.split_once("</system-reminder>") else {
            return false;
        };
        remaining = rest.trim_start();
        if remaining.is_empty() {
            return true;
        }
    }
    false
}
