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

const HOST_BLOCK_TAGS: [&str; 4] = [
    "system-reminder",
    "conversation_history_summary",
    "cb_summary",
    "task-notification",
];

fn strip_host_block(text: &str) -> Option<&str> {
    for tag in HOST_BLOCK_TAGS {
        let Some(after_tag) = text
            .strip_prefix('<')
            .and_then(|value| value.strip_prefix(tag))
        else {
            continue;
        };
        // The boundary matters: <task-notification-extra> is user text, not a host block.
        if !after_tag.starts_with('>') && !after_tag.chars().next().is_some_and(char::is_whitespace)
        {
            continue;
        }
        let open_end = after_tag.find('>')?;
        if after_tag[..open_end].contains('<') {
            return None;
        }
        let closing = format!("</{tag}>");
        return after_tag[open_end + 1..]
            .split_once(closing.as_str())
            .map(|(_, remaining)| remaining);
    }
    None
}

fn is_reminder_text(text: &str) -> bool {
    let mut remaining = text.trim();
    if remaining.is_empty() {
        return false;
    }
    while let Some(rest) = strip_host_block(remaining) {
        remaining = rest.trim_start();
        if remaining.is_empty() {
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::{is_reminder_only, is_reminder_text, HOST_BLOCK_TAGS};
    use serde_json::json;

    #[test]
    fn standalone_host_blocks_do_not_select_a_new_turn() {
        for tag in HOST_BLOCK_TAGS {
            let block = format!("<{tag}>host content</{tag}>");
            assert!(is_reminder_text(&block), "{tag}");
        }
        assert!(is_reminder_text(
            " <system-reminder>status</system-reminder>\n<cb_summary>old turn</cb_summary>\n<task-notification id=\"1\">done</task-notification> "
        ));
        assert!(is_reminder_only(&[
            json!({"type": "input_text", "text": "<conversation_history_summary>old turn</conversation_history_summary>"}),
            json!({"type": "input_text", "text": "<cb_summary>compact</cb_summary>"}),
        ]));
    }

    #[test]
    fn user_prompts_and_malformed_blocks_are_not_discarded() {
        for text in [
            "Please explain <cb_summary>summary</cb_summary>",
            "<cb_summary>summary</cb_summary> and respond",
            "<task-notification-extra>not a host wrapper</task-notification-extra>",
            "<conversation_history_summary>missing close",
            "<system-reminder>unfinished",
            "<cb_summary>old</cb_summary><task-notification>unfinished",
            "",
        ] {
            assert!(!is_reminder_text(text), "{text}");
        }
        assert!(!is_reminder_only(&[
            json!({"type": "input_text", "text": "<cb_summary>old</cb_summary>"}),
            json!({"type": "input_text", "text": "What changed?"}),
        ]));
        assert!(!is_reminder_only(&[
            json!({"type": "input_text", "text": "<task-notification>update</task-notification>"}),
            json!({"type": "input_image", "image_url": "https://example.com/image.png"}),
        ]));
    }
}
