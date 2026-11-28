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

//! 入口请求的回合类型。判定只依赖请求尾部形态，供证据、日志与召回决策共用。

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TurnKind {
    /// 最后一条是用户消息：新的用户回合。
    UserTurn,
    /// 最后一条是工具结果或工具调用条目：工具循环续写。
    /// Responses 的 `function_call`、`*_call_output` 与 MCP 审批往返归入此类型。
    ToolContinuation,
    /// 最后一条是不带工具调用的助手消息或助手侧条目（如 Responses 的 `reasoning`）：续写或预填充。
    AssistantOnly,
    /// 没有消息，或最后一条是 system / developer。
    Empty,
}

impl TurnKind {
    /// 稳定的日志字段值与指标口径；与序列化名称一致。
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::UserTurn => "user_turn",
            Self::ToolContinuation => "tool_continuation",
            Self::AssistantOnly => "assistant_only",
            Self::Empty => "empty",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn log_names_match_serialized_names() {
        for kind in [
            TurnKind::UserTurn,
            TurnKind::ToolContinuation,
            TurnKind::AssistantOnly,
            TurnKind::Empty,
        ] {
            assert_eq!(
                serde_json::to_value(kind).unwrap(),
                serde_json::Value::String(kind.as_str().into())
            );
        }
    }
}
