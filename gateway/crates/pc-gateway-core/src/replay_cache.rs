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

//! 会话内复用准备结果（RFC 2199：新用户消息最多触发一次准备，工具续写精确重放）。
//!
//! 状态按 Scope + 宿主提供的会话 ID（`X-PowerContext-Conversation-ID`）分区；没有会话 ID 的请求
//! 不读也不写这里，不会继承任何旧注入。会话内用"消息锚点"（截至最后一条用户消息的原始消息摘要）
//! 区分工具续写（锚点相同 → 重放同一内容与绑定）和新的用户消息（锚点变化 → 准备一次）。
//! 锚点只在可信会话内比较，不用作会话身份。

use pc_gateway_protocol::CanonicalRequest;
use std::collections::hash_map::DefaultHasher;
use std::collections::VecDeque;
use std::hash::{Hash, Hasher};
use std::sync::Mutex;

/// 会话条目上限：超出后淘汰最旧的会话。
const MAX_CONVERSATIONS: usize = 1024;
/// 单个准备结果的最长保留时间；复用绑定更早到期时以绑定为准。
pub(crate) const MAX_REPLAY_MS: u64 = 5 * 60 * 1000;

/// 一次准备的结果。
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct PreparedBlock {
    pub block: String,
    pub binding: String,
}

struct Entry {
    scope_id: String,
    conversation_id: String,
    anchor: u64,
    /// `None` 表示 Core 返回空上下文：同样复用，不重复检索。
    prepared: Option<PreparedBlock>,
    expires_ms: u64,
}

#[derive(Default)]
pub(crate) struct ReplayCache {
    entries: Mutex<VecDeque<Entry>>,
}

/// 消息锚点：截至最后一条用户消息（含）的原始消息摘要；没有用户消息时为 `None`。
pub(crate) fn message_anchor(request: &CanonicalRequest) -> Option<u64> {
    let last_user = request
        .messages
        .iter()
        .rposition(|m| matches!(m.role, pc_gateway_protocol::Role::User))?;
    let prefix = serde_json::to_string(&request.messages[..=last_user]).ok()?;
    let mut hasher = DefaultHasher::new();
    prefix.hash(&mut hasher);
    Some(hasher.finish())
}

impl ReplayCache {
    /// 同一会话、同一锚点、未过期的结果：`Some(None)` 表示已知为空，`None` 表示需要准备。
    pub(crate) fn get(
        &self,
        scope_id: &str,
        conversation_id: &str,
        anchor: u64,
        now_ms: u64,
    ) -> Option<Option<PreparedBlock>> {
        let mut entries = lock(&self.entries);
        entries.retain(|e| e.expires_ms > now_ms);
        entries
            .iter()
            .find(|e| {
                e.scope_id == scope_id && e.conversation_id == conversation_id && e.anchor == anchor
            })
            .map(|e| e.prepared.clone())
    }

    /// 写入一个会话当前锚点的结果（put-if-absent）：并发请求已写入同一锚点时保留先写入的值并返回它，
    /// 保证同一锚点只有一种注入内容。新锚点替换该会话的旧锚点。
    pub(crate) fn put_if_absent(
        &self,
        scope_id: &str,
        conversation_id: &str,
        anchor: u64,
        prepared: Option<PreparedBlock>,
        expires_ms: u64,
    ) -> Option<PreparedBlock> {
        let mut entries = lock(&self.entries);
        if let Some(existing) = entries.iter().find(|e| {
            e.scope_id == scope_id && e.conversation_id == conversation_id && e.anchor == anchor
        }) {
            return existing.prepared.clone();
        }
        entries.retain(|e| !(e.scope_id == scope_id && e.conversation_id == conversation_id));
        if entries.len() >= MAX_CONVERSATIONS {
            entries.pop_front();
        }
        entries.push_back(Entry {
            scope_id: scope_id.into(),
            conversation_id: conversation_id.into(),
            anchor,
            prepared: prepared.clone(),
            expires_ms,
        });
        prepared
    }
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn block(text: &str) -> Option<PreparedBlock> {
        Some(PreparedBlock {
            block: text.into(),
            binding: "b".into(),
        })
    }

    #[test]
    fn entries_expire_and_are_bounded() {
        let cache = ReplayCache::default();
        cache.put_if_absent("s", "c", 1, block("x"), 100);
        assert_eq!(cache.get("s", "c", 1, 50), Some(block("x")));
        assert_eq!(cache.get("s", "c", 1, 100), None);
        for i in 0..=MAX_CONVERSATIONS {
            cache.put_if_absent("s", &format!("c{i}"), 1, None, 1_000);
        }
        assert_eq!(
            cache.get("s", "c0", 1, 0),
            None,
            "the oldest conversation is evicted"
        );
        assert_eq!(
            cache.get("s", &format!("c{MAX_CONVERSATIONS}"), 1, 0),
            Some(None)
        );
    }

    #[test]
    fn the_first_value_for_an_anchor_wins() {
        let cache = ReplayCache::default();
        assert_eq!(
            cache.put_if_absent("s", "c", 1, block("first"), 1_000),
            block("first")
        );
        assert_eq!(
            cache.put_if_absent("s", "c", 1, block("second"), 1_000),
            block("first")
        );
    }

    #[test]
    fn conversations_and_scopes_are_isolated() {
        let cache = ReplayCache::default();
        cache.put_if_absent("s", "a", 1, block("a"), 1_000);
        assert_eq!(cache.get("s", "b", 1, 0), None);
        assert_eq!(cache.get("other", "a", 1, 0), None);
    }

    #[test]
    fn a_new_anchor_replaces_the_conversation_entry() {
        let cache = ReplayCache::default();
        cache.put_if_absent("s", "c", 1, block("old"), 1_000);
        cache.put_if_absent("s", "c", 2, block("new"), 1_000);
        assert_eq!(cache.get("s", "c", 1, 0), None);
        assert_eq!(cache.get("s", "c", 2, 0), Some(block("new")));
    }
}
