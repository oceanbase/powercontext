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

//! 运维配置的固定指令（`PC_GATEWAY_STEERING_FILE`），只插入 Responses 入口的请求。
//!
//! 文本在启动时读取并校验一次；每个请求插入同一位置的同一文本，因此它是稳定前缀的一部分，
//! 首个请求之后按缓存读取计费。它与上下文注入、沉淀和准备结果的重放相互独立。

use serde_json::Value;

/// 指令文本的字节上限。
pub const MAX_STEERING_BYTES: usize = 4096;

/// PowerContext 保留的标记前缀。Core 只接受出现在已声明的准备结果范围内的此类标记，
/// 指令文本含有它时每个请求都会被拒绝，所以启动时就拒绝。
const RESERVED_MARKER_PREFIX: &str = "BEGIN_POWERCONTEXT_";

/// 读取并校验指令文件：未配置时返回 `None`；文件不可读、不是 UTF-8、去掉首尾空白后为空、超过
/// [`MAX_STEERING_BYTES`] 或含有 PowerContext 保留标记时返回错误，网关据此拒绝启动，而不是不带指令运行。
pub fn load(path: Option<&str>) -> Result<Option<String>, String> {
    let Some(path) = path else {
        return Ok(None);
    };
    let bytes = std::fs::read(path).map_err(|_| "file cannot be read".to_string())?;
    let text = String::from_utf8(bytes).map_err(|_| "file is not UTF-8".to_string())?;
    let text = text.trim();
    if text.is_empty() {
        return Err("file is empty".into());
    }
    if text.len() > MAX_STEERING_BYTES {
        return Err(format!("text exceeds {MAX_STEERING_BYTES} bytes"));
    }
    if text.contains(RESERVED_MARKER_PREFIX) {
        return Err("text contains a reserved PowerContext marker".into());
    }
    Ok(Some(text.to_owned()))
}

/// 把指令放在客户端开发者指令的位置（Codex 放 `developer_instructions` 的位置）：紧跟客户端基础提示
/// （第一条 developer 消息）的那条 developer 消息，作为它的第一个内容块。基础提示后面没有 developer 消息时，
/// 在基础提示后插入一条新的 developer 消息；没有 developer 消息时，插在第一条用户消息之前（没有用户消息则在末尾）。
pub fn insert(input: &mut Vec<Value>, text: &str) {
    let block = serde_json::json!({"type": "input_text", "text": text});
    let Some(base) = input.iter().position(|item| is_message(item, "developer")) else {
        let index = input
            .iter()
            .position(|item| is_message(item, "user"))
            .unwrap_or(input.len());
        input.insert(index, developer_message(block));
        return;
    };
    if let Some(content) = input
        .get_mut(base + 1)
        .filter(|item| is_message(item, "developer"))
        .and_then(|item| item.get_mut("content"))
        .and_then(Value::as_array_mut)
    {
        content.insert(0, block);
        return;
    }
    input.insert(base + 1, developer_message(block));
}

fn is_message(item: &Value, role: &str) -> bool {
    item.get("type")
        .and_then(Value::as_str)
        .unwrap_or("message")
        == "message"
        && item.get("role").and_then(Value::as_str) == Some(role)
}

fn developer_message(block: Value) -> Value {
    serde_json::json!({"type": "message", "role": "developer", "content": [block]})
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn developer(text: &str) -> Value {
        json!({"type": "message", "role": "developer", "content": [{"type": "input_text", "text": text}]})
    }

    fn user(text: &str) -> Value {
        json!({"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})
    }

    #[test]
    fn the_file_is_validated_at_load() {
        let dir = std::env::temp_dir().join(format!("pc-steering-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = |name: &str, bytes: &[u8]| {
            let path = dir.join(name);
            std::fs::write(&path, bytes).unwrap();
            path.to_string_lossy().into_owned()
        };
        assert_eq!(load(None), Ok(None));
        assert_eq!(
            load(Some(&file("ok", b"  Plan before acting.\n"))),
            Ok(Some("Plan before acting.".into()))
        );
        assert!(load(Some(&file("latin1", b"caf\xe9"))).is_err());
        assert!(load(Some(&file("blank", b" \n\t"))).is_err());
        assert!(load(Some(&file("large", &[b'a'; MAX_STEERING_BYTES + 1]))).is_err());
        assert!(load(Some(&file("limit", &[b'a'; MAX_STEERING_BYTES]))).is_ok());
        assert!(load(Some(&file(
            "marker",
            b"Plan. BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
        )))
        .is_err());
        assert!(load(Some(&dir.join("missing").to_string_lossy())).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn it_leads_the_developer_message_after_the_base_prompt() {
        // Codex 0.145 的形状：additional_tools 也带 developer 角色，但不是消息。
        let tools = json!({"type": "additional_tools", "role": "developer", "tools": []});
        let mut input = vec![
            tools.clone(),
            developer("base"),
            developer("permissions"),
            user("task"),
        ];
        insert(&mut input, "be brief");
        assert_eq!(input.len(), 4);
        assert_eq!(input[0], tools);
        assert_eq!(input[1], developer("base"));
        assert_eq!(
            input[2]["content"],
            json!([
                {"type": "input_text", "text": "be brief"},
                {"type": "input_text", "text": "permissions"}
            ])
        );
        assert_eq!(input[3], user("task"));
    }

    #[test]
    fn it_falls_back_to_a_new_developer_message() {
        let mut after_base = vec![developer("base"), user("task")];
        insert(&mut after_base, "be brief");
        assert_eq!(
            after_base,
            vec![developer("base"), developer("be brief"), user("task")]
        );

        let mut no_developer = vec![
            json!({"type": "function_call_output", "call_id": "c", "output": "ok"}),
            user("task"),
        ];
        insert(&mut no_developer, "be brief");
        assert_eq!(no_developer[1], developer("be brief"));
        assert_eq!(no_developer[2], user("task"));

        let mut no_user: Vec<Value> = Vec::new();
        insert(&mut no_user, "be brief");
        assert_eq!(no_user, vec![developer("be brief")]);
    }

    #[test]
    fn requests_of_one_conversation_keep_a_common_prefix() {
        let first_input = vec![developer("base"), developer("permissions"), user("task")];
        let mut later_input = first_input.clone();
        later_input.push(json!({"type": "function_call_output", "call_id": "c", "output": "ok"}));
        let (mut first, mut later) = (first_input, later_input);
        insert(&mut first, "be brief");
        insert(&mut later, "be brief");
        assert_eq!(first[..], later[..first.len()]);
    }
}
