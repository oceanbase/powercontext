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

//! 入口请求尾部的回合类型判定。
//!
//! 判定只使用规范化消息的角色、工具调用是否存在，以及 Responses 透传 item 自身的
//! `role`/`type`；不读取正文，也不调用模型。Responses 入口把非用户 item 原样封装为
//! `Role::Tool` 承载（渲染器依赖这一形态逐字透出），因此该形态必须回到 item 本身取语义角色。
//!
//! 判定锚点是最后一条用户消息，而不是整个列表的最后一条：客户端与网关固定指令会在用户
//! 提问之后追加 developer/system 提示，这类尾部不构成新回合，也不应隐藏该用户回合。

use crate::render::RESPONSES_LIST_KEY;
use crate::{CanonicalMessage, CanonicalRequest, Role};
use pc_gateway_types::TurnKind;
use serde_json::Value;

/// 按请求尾部判定回合类型。
///
/// 以最后一条用户消息为锚点，由其后的尾部决定类型：尾部只有 system/developer 时仍是该用户回合；
/// 出现工具条目或助手消息时按它们判定。找不到用户消息时退回按最后一条消息判定。
///
/// `list_key` 取自实际使用的 renderer。入口 format 决定 renderer，二者目前一一对应；若 renderer
/// 将来改为按上游协议选择，需要把入口协议显式传入，不能继续由渲染形态推断。
pub fn turn_kind(request: &CanonicalRequest, list_key: &str) -> TurnKind {
    let Some(last_user) = request
        .messages
        .iter()
        .rposition(|message| is_user_message(message, list_key))
    else {
        return request
            .messages
            .last()
            .map_or(TurnKind::Empty, |message| classify(message, list_key));
    };
    match request.messages[last_user + 1..]
        .iter()
        .rev()
        .find(|message| !is_system_like(message, list_key))
    {
        Some(message) => classify(message, list_key),
        None => TurnKind::UserTurn,
    }
}

fn classify(message: &CanonicalMessage, list_key: &str) -> TurnKind {
    match message.role {
        Role::User => TurnKind::UserTurn,
        Role::Assistant if has_tool_calls(message) => TurnKind::ToolContinuation,
        Role::Assistant => TurnKind::AssistantOnly,
        Role::System | Role::Developer => TurnKind::Empty,
        Role::Tool if list_key == RESPONSES_LIST_KEY => responses_item_turn_kind(&message.content),
        Role::Tool => TurnKind::ToolContinuation,
    }
}

/// Responses 入口把 item 封装在 `Role::Tool` 里，用户消息同样需要回到 item 自身取角色。
fn is_user_message(message: &CanonicalMessage, list_key: &str) -> bool {
    match message.role {
        Role::User => true,
        Role::Tool if list_key == RESPONSES_LIST_KEY => {
            responses_item_role(&message.content) == Some("user")
        }
        _ => false,
    }
}

fn is_system_like(message: &CanonicalMessage, list_key: &str) -> bool {
    match message.role {
        Role::System | Role::Developer => true,
        Role::Tool if list_key == RESPONSES_LIST_KEY => matches!(
            responses_item_role(&message.content),
            Some("system" | "developer")
        ),
        _ => false,
    }
}

fn responses_item_role(content: &[Value]) -> Option<&str> {
    let [Value::Object(item)] = content else {
        return None;
    };
    item.get("role").and_then(Value::as_str)
}

fn has_tool_calls(message: &CanonicalMessage) -> bool {
    message
        .tool_calls
        .as_ref()
        .is_some_and(|calls| !calls.is_empty())
}

/// Responses 入口把每个非用户 item 封装成单元素 content。content 不是单个对象时无法还原语义，
/// 按空回合处理；否则按 item 自身的 `role`/`type` 还原，而不是沿用承载它的 `Role::Tool`。
fn responses_item_turn_kind(content: &[Value]) -> TurnKind {
    let [Value::Object(item)] = content else {
        return TurnKind::Empty;
    };
    if let Some(role) = item.get("role").and_then(Value::as_str) {
        return match role {
            "assistant" => TurnKind::AssistantOnly,
            "user" => TurnKind::UserTurn,
            // system、developer 以及未知的非用户角色都不是用户回合。
            _ => TurnKind::Empty,
        };
    }
    let item_type = item.get("type").and_then(Value::as_str).unwrap_or("");
    if is_tool_item(item_type) {
        TurnKind::ToolContinuation
    } else {
        // reasoning、item_reference、工具清单、工具定义等助手或开发者侧条目：
        // 既不是工具续写，也不跳过召回。
        TurnKind::AssistantOnly
    }
}

/// 需要客户端执行的工具条目：客户端工具调用、工具结果与 MCP 审批往返，与 `transcript::classify`
/// 的客户端条目同词汇；服务商在响应内执行完毕的内置工具不在此列。
const TOOL_ITEMS: &[&str] = &[
    "function_call",
    "custom_tool_call",
    "computer_call",
    "local_shell_call",
    "apply_patch_call",
    "function_call_output",
    "custom_tool_call_output",
    "computer_call_output",
    "local_shell_call_output",
    "apply_patch_call_output",
    "mcp_approval_request",
    "mcp_approval_response",
];

/// 使用显式清单而不是后缀匹配，避免普通条目名以 `_call` 结尾就被当作工具续写。
fn is_tool_item(item_type: &str) -> bool {
    TOOL_ITEMS.contains(&item_type)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::OutputLimits;

    fn message(role: Role, tool_calls: Option<Vec<Value>>) -> CanonicalMessage {
        CanonicalMessage {
            role,
            content: vec![Value::from("text")],
            content_null: false,
            tool_calls,
            tool_call_id: None,
            extra_fields: Default::default(),
        }
    }

    /// Responses 入口封装的透传 item：单元素 content，承载角色为 `Role::Tool`。
    fn item(value: Value) -> CanonicalMessage {
        CanonicalMessage {
            role: Role::Tool,
            content: vec![value],
            content_null: false,
            tool_calls: None,
            tool_call_id: None,
            extra_fields: Default::default(),
        }
    }

    fn request(messages: Vec<CanonicalMessage>) -> CanonicalRequest {
        CanonicalRequest {
            model: "test-model".into(),
            messages,
            tools: vec![],
            instructions: None,
            native_cache_fields: None,
            output_limits: OutputLimits {
                max_output_tokens: Some(128),
            },
            stream: false,
            response_format: None,
            tool_choice: None,
            store: None,
            previous_response_id: None,
            conversation: None,
            passthrough_fields: None,
            token_limit_field: None,
        }
    }

    fn response_message(role: &str) -> Value {
        serde_json::json!({"type": "message", "role": role, "content": []})
    }

    #[test]
    fn chat_roles_decide_the_turn_kind() {
        let tool_call = serde_json::json!({"id": "call_1", "type": "function"});
        for (messages, expected) in [
            (vec![], TurnKind::Empty),
            (vec![message(Role::System, None)], TurnKind::Empty),
            (vec![message(Role::Developer, None)], TurnKind::Empty),
            (vec![message(Role::User, None)], TurnKind::UserTurn),
            (
                // 用户提问之后追加的提示不隐藏该用户回合。
                vec![message(Role::User, None), message(Role::Developer, None)],
                TurnKind::UserTurn,
            ),
            (
                vec![message(Role::User, None), message(Role::System, None)],
                TurnKind::UserTurn,
            ),
            (
                vec![
                    message(Role::User, None),
                    message(Role::Assistant, Some(vec![])),
                    message(Role::Developer, None),
                ],
                TurnKind::AssistantOnly,
            ),
            (vec![message(Role::Tool, None)], TurnKind::ToolContinuation),
            (
                vec![message(Role::Assistant, Some(vec![tool_call.clone()]))],
                TurnKind::ToolContinuation,
            ),
            (
                vec![message(Role::Assistant, Some(vec![]))],
                TurnKind::AssistantOnly,
            ),
            (
                vec![message(Role::Assistant, None)],
                TurnKind::AssistantOnly,
            ),
            (
                vec![
                    message(Role::User, None),
                    message(Role::Assistant, Some(vec![tool_call])),
                    message(Role::Tool, None),
                ],
                TurnKind::ToolContinuation,
            ),
        ] {
            assert_eq!(turn_kind(&request(messages), "messages"), expected);
        }
    }

    #[test]
    fn responses_items_are_classified_by_their_own_role_and_type() {
        for (messages, expected) in [
            (vec![], TurnKind::Empty),
            (vec![message(Role::User, None)], TurnKind::UserTurn),
            (
                vec![item(response_message("assistant"))],
                TurnKind::AssistantOnly,
            ),
            (
                // 类型省略的消息 item 同样是助手消息。
                vec![item(
                    serde_json::json!({"role": "assistant", "content": "ok"}),
                )],
                TurnKind::AssistantOnly,
            ),
            (vec![item(response_message("developer"))], TurnKind::Empty),
            (vec![item(response_message("system"))], TurnKind::Empty),
            (
                vec![item(
                    serde_json::json!({"role": "developer", "content": "reminder"}),
                )],
                TurnKind::Empty,
            ),
            (
                // 新用户提问之后跟一条 developer 提示：尾部仍是该用户回合。
                vec![
                    message(Role::User, None),
                    item(serde_json::json!({"role": "developer", "content": "reminder"})),
                ],
                TurnKind::UserTurn,
            ),
            (
                vec![
                    message(Role::User, None),
                    item(serde_json::json!({"role": "system", "content": "s"})),
                ],
                TurnKind::UserTurn,
            ),
            (
                // 提示之后出现工具条目时仍是工具续写。
                vec![
                    message(Role::User, None),
                    item(serde_json::json!({"role": "developer", "content": "reminder"})),
                    item(serde_json::json!({"type": "function_call_output", "output": "ok"})),
                ],
                TurnKind::ToolContinuation,
            ),
            (
                vec![item(
                    serde_json::json!({"type": "function_call", "name": "run"}),
                )],
                TurnKind::ToolContinuation,
            ),
            (
                vec![item(
                    serde_json::json!({"type": "function_call_output", "output": "ok"}),
                )],
                TurnKind::ToolContinuation,
            ),
            (
                vec![item(serde_json::json!({"type": "mcp_approval_request"}))],
                TurnKind::ToolContinuation,
            ),
            (
                vec![item(serde_json::json!({"type": "mcp_approval_response"}))],
                TurnKind::ToolContinuation,
            ),
            (
                // 名称包含 call 但不是工具条目。
                vec![item(serde_json::json!({"type": "recall"}))],
                TurnKind::AssistantOnly,
            ),
            (
                // 自定义类型以 _call 结尾也不在工具清单里。
                vec![item(serde_json::json!({"type": "my_hook_call"}))],
                TurnKind::AssistantOnly,
            ),
            (
                // 服务商在响应内执行完毕的内置工具不是客户端工具续写。
                vec![item(serde_json::json!({"type": "web_search_call"}))],
                TurnKind::AssistantOnly,
            ),
            (
                // content 不是单个 item 对象时无法还原语义。
                vec![message(Role::Tool, None)],
                TurnKind::Empty,
            ),
            (
                vec![item(
                    serde_json::json!({"type": "reasoning", "summary": []}),
                )],
                TurnKind::AssistantOnly,
            ),
            (
                vec![item(
                    serde_json::json!({"type": "additional_tools", "tools": []}),
                )],
                TurnKind::AssistantOnly,
            ),
        ] {
            assert_eq!(turn_kind(&request(messages), "input"), expected);
        }
    }

    /// 工具清单里的每个条目都必须判为工具续写。清单在这里独立写出，常量拼错时用例失败，
    /// 而不是静默降级成助手回合。
    #[test]
    fn every_client_tool_item_is_a_tool_continuation() {
        for item_type in [
            "function_call",
            "custom_tool_call",
            "computer_call",
            "local_shell_call",
            "apply_patch_call",
            "function_call_output",
            "custom_tool_call_output",
            "computer_call_output",
            "local_shell_call_output",
            "apply_patch_call_output",
            "mcp_approval_request",
            "mcp_approval_response",
        ] {
            assert_eq!(
                turn_kind(
                    &request(vec![item(serde_json::json!({ "type": item_type }))]),
                    "input"
                ),
                TurnKind::ToolContinuation,
                "{item_type}"
            );
        }
    }
}
