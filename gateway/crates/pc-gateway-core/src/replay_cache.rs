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
//! 状态按 Scope + 重放会话 ID 分区；没有会话 ID 的请求
//! 不读也不写这里，不会继承任何旧注入。每条用户消息的锚点是截至该消息的原始消息摘要。
//! 在后来请求中重放仍匹配的块，只有最新的用户消息可以触发准备。
//! 锚点只在同一重放会话内比较，不用作会话身份。

use pc_gateway_protocol::{CanonicalRequest, Role};
use std::collections::hash_map::RandomState;
use std::collections::VecDeque;
use std::hash::{BuildHasher, Hash, Hasher};
use std::sync::Mutex;

/// Global FIFO bound; within a matching conversation prefix prefer retaining earlier anchors.
const MAX_ENTRIES: usize = 1024;
const MAX_ANCHORS_PER_CONVERSATION: usize = 8;
/// Leave a provider-attempt-sized margin before replaying historical context.
const HISTORY_EXPIRY_MARGIN_MS: u64 = 120_000;
/// Bound historical recall separately from the current turn's prepare budget.
const MAX_HISTORY_BYTES: usize = 8192;
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
    anchor_hasher: RandomState,
}

impl ReplayCache {
    /// Hash each original message once. A prefix identifies its position even
    /// when the same user text appears several times in a conversation.
    pub(crate) fn message_anchors(
        &self,
        request: &CanonicalRequest,
        list_key: &str,
    ) -> Vec<(usize, u64)> {
        let Some(last_user) = request.messages.iter().rposition(|m| m.role == Role::User) else {
            return Vec::new();
        };
        let mut hasher = self.anchor_hasher.build_hasher();
        (
            list_key,
            &request.instructions,
            &request.previous_response_id,
            &request.conversation,
        )
            .hash(&mut hasher);
        let mut anchors = Vec::new();
        let mut encoded = Vec::new();
        for (index, message) in request.messages[..=last_user].iter().enumerate() {
            encoded.clear();
            if serde_json::to_writer(&mut encoded, message).is_err() {
                return Vec::new();
            }
            encoded.hash(&mut hasher);
            if message.role == Role::User {
                anchors.push((index, hasher.finish()));
            }
        }
        anchors
    }

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

    /// Return the retained historical blocks in the order Core checks their locations.
    pub(crate) fn history(
        &self,
        scope_id: &str,
        conversation_id: &str,
        anchors: &[(usize, u64)],
        now_ms: u64,
    ) -> Vec<(usize, PreparedBlock)> {
        if anchors.is_empty() {
            return Vec::new();
        }
        let mut entries = lock(&self.entries);
        entries.retain(|e| e.expires_ms > now_ms);
        let mut blocks: Vec<_> = entries
            .iter()
            .filter(|e| {
                e.scope_id == scope_id
                    && e.conversation_id == conversation_id
                    && e.expires_ms > now_ms.saturating_add(HISTORY_EXPIRY_MARGIN_MS)
            })
            .filter_map(|e| {
                let prepared = e.prepared.as_ref()?;
                let (index, _) = anchors.iter().find(|(_, anchor)| *anchor == e.anchor)?;
                Some((*index, prepared.clone()))
            })
            .collect();
        blocks.sort_unstable_by_key(|(index, _)| *index);
        let mut remaining = MAX_HISTORY_BYTES;
        blocks
            .into_iter()
            .take_while(|(_, prepared)| {
                if prepared.block.len() > remaining {
                    return false;
                }
                remaining -= prepared.block.len();
                true
            })
            .collect()
    }

    /// Invalidate the matching replay window when Core cannot identify a stale binding.
    pub(crate) fn forget_matching(
        &self,
        scope_id: &str,
        conversation_id: &str,
        anchors: &[(usize, u64)],
    ) {
        lock(&self.entries).retain(|e| {
            !(e.scope_id == scope_id
                && e.conversation_id == conversation_id
                && anchors.iter().any(|(_, anchor)| *anchor == e.anchor))
        });
    }

    /// 写入一个锚点的结果（put-if-absent）：已有同一锚点时保留先写入的值并返回它，保证同一锚点只有
    /// 一种注入内容。同一会话的不同锚点并存、不互相替换，上一回合迟到完成的准备不会挤掉当前回合。
    pub(crate) fn put_if_absent(
        &self,
        scope_id: &str,
        conversation_id: &str,
        anchor: u64,
        prepared: Option<PreparedBlock>,
        expires_ms: u64,
        history: &[(usize, u64)],
    ) -> Option<PreparedBlock> {
        let mut entries = lock(&self.entries);
        if let Some(existing) = entries.iter().find(|e| {
            e.scope_id == scope_id && e.conversation_id == conversation_id && e.anchor == anchor
        }) {
            return existing.prepared.clone();
        }
        let same_conversation =
            |e: &Entry| e.scope_id == scope_id && e.conversation_id == conversation_id;
        if entries.iter().filter(|e| same_conversation(e)).count() >= MAX_ANCHORS_PER_CONVERSATION {
            // Prefer the latest matching historical anchor: replacing an earlier
            // one would invalidate a larger part of the provider's cached prefix.
            let newest_history = history.iter().rev().find_map(|(_, anchor)| {
                entries
                    .iter()
                    .position(|e| same_conversation(e) && e.anchor == *anchor)
            });
            if let Some(index) = newest_history.or_else(|| entries.iter().position(same_conversation))
            {
                entries.remove(index);
            }
        }
        if entries.len() >= MAX_ENTRIES {
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
        cache.put_if_absent("s", "c", 1, block("x"), 100, &[]);
        assert_eq!(cache.get("s", "c", 1, 50), Some(block("x")));
        assert_eq!(cache.get("s", "c", 1, 100), None);
        for i in 0..=MAX_ENTRIES {
            cache.put_if_absent("s", &format!("c{i}"), 1, None, 1_000, &[]);
        }
        assert_eq!(
            cache.get("s", "c0", 1, 0),
            None,
            "the oldest entry is evicted"
        );
        assert_eq!(cache.get("s", &format!("c{MAX_ENTRIES}"), 1, 0), Some(None));
    }

    #[test]
    fn the_first_value_for_an_anchor_wins() {
        let cache = ReplayCache::default();
        assert_eq!(
            cache.put_if_absent("s", "c", 1, block("first"), 1_000, &[]),
            block("first")
        );
        assert_eq!(
            cache.put_if_absent("s", "c", 1, block("second"), 1_000, &[]),
            block("first")
        );
    }

    #[test]
    fn conversations_and_scopes_are_isolated() {
        let cache = ReplayCache::default();
        cache.put_if_absent("s", "a", 1, block("a"), 1_000, &[]);
        assert_eq!(cache.get("s", "b", 1, 0), None);
        assert_eq!(cache.get("other", "a", 1, 0), None);
    }

    #[test]
    fn history_skips_near_expiry_and_respects_the_byte_budget() {
        let cache = ReplayCache::default();
        let large = "x".repeat(MAX_HISTORY_BYTES - 1);
        let expires = 300_000;
        cache.put_if_absent("s", "c", 1, block("stale"), HISTORY_EXPIRY_MARGIN_MS, &[]);
        cache.put_if_absent("s", "c", 2, block(&large), expires, &[]);
        cache.put_if_absent("s", "c", 3, block("xx"), expires, &[]);
        let anchors = [(0, 1), (2, 2), (4, 3)];
        assert_eq!(
            cache.history("s", "c", &anchors, 0),
            vec![(2, block(&large).unwrap())]
        );
        assert_eq!(cache.get("s", "c", 1, 0), Some(block("stale")));
    }

    #[test]
    fn a_full_matching_history_prefers_early_anchors() {
        let cache = ReplayCache::default();
        let mut history = Vec::new();
        for anchor in 0..MAX_ANCHORS_PER_CONVERSATION as u64 {
            cache.put_if_absent("s", "c", anchor, block("x"), 300_000, &history);
            history.push((anchor as usize, anchor));
        }
        let next = MAX_ANCHORS_PER_CONVERSATION as u64;
        cache.put_if_absent("s", "c", next, block("new"), 300_000, &history);
        assert_eq!(cache.get("s", "c", 0, 0), Some(block("x")));
        assert_eq!(cache.get("s", "c", next - 1, 0), None);
        assert_eq!(cache.get("s", "c", next, 0), Some(block("new")));
    }

    #[test]
    fn anchors_of_one_conversation_coexist_within_a_bound() {
        let cache = ReplayCache::default();
        cache.put_if_absent("s", "c", 2, block("current"), 1_000, &[]);
        // A late completion for an earlier anchor must not replace the current one.
        cache.put_if_absent("s", "c", 1, block("late"), 1_000, &[]);
        assert_eq!(cache.get("s", "c", 2, 0), Some(block("current")));
        assert_eq!(cache.get("s", "c", 1, 0), Some(block("late")));
        for anchor in 10..(10 + MAX_ANCHORS_PER_CONVERSATION as u64) {
            cache.put_if_absent("s", "c", anchor, None, 1_000, &[]);
        }
        assert_eq!(
            cache.get("s", "c", 2, 0),
            None,
            "the conversation keeps its newest anchors"
        );
    }
}
