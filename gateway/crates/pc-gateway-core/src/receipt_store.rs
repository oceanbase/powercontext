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

//! 会话内的加密内容回执（#2420）。
//!
//! Core 在 before_release 放行带加密内容的响应时返回回执，记录这些密文的摘要。之后同一会话（以及由它派生的子代理
//! 会话）的请求重放这些密文时，网关把回执随 before_send 一起交给 Core 验证。回执由 Core 签名，网关只负责按会话
//! 保存和转交；状态在内存中，有上限，过期后丢弃。

use std::collections::VecDeque;
use std::sync::Mutex;

/// 每个会话保留的回执数，以及一次 before_send 最多转交的回执数（与 Core 的上限一致）。
pub(crate) const MAX_RECEIPTS: usize = 32;
/// 会话数上限：超出后淘汰最早写入的会话。
const MAX_CONVERSATIONS: usize = 1024;
/// 回执在网关保留的时长，与 Core 的有效期一致。
pub(crate) const RECEIPT_TTL_MS: u64 = 60 * 60 * 1000;

struct Conversation {
    scope_id: String,
    conversation_id: String,
    receipts: VecDeque<(String, u64)>,
}

#[derive(Default)]
pub(crate) struct ReceiptStore {
    conversations: Mutex<VecDeque<Conversation>>,
}

impl ReceiptStore {
    pub(crate) fn add(&self, scope_id: &str, conversation_id: &str, receipt: String, now_ms: u64) {
        let mut conversations = lock(&self.conversations);
        let index = conversations
            .iter()
            .position(|c| c.scope_id == scope_id && c.conversation_id == conversation_id);
        let mut conversation = match index.and_then(|i| conversations.remove(i)) {
            Some(existing) => existing,
            None => Conversation {
                scope_id: scope_id.into(),
                conversation_id: conversation_id.into(),
                receipts: VecDeque::new(),
            },
        };
        conversation
            .receipts
            .retain(|(_, expires)| *expires > now_ms);
        if conversation.receipts.len() >= MAX_RECEIPTS {
            conversation.receipts.pop_front();
        }
        conversation
            .receipts
            .push_back((receipt, now_ms + RECEIPT_TTL_MS));
        if conversations.len() >= MAX_CONVERSATIONS {
            conversations.pop_front();
        }
        conversations.push_back(conversation);
    }

    /// 这次请求要转交的回执：先取当前会话的，再取父会话的，最新的优先，总数不超过 [`MAX_RECEIPTS`]。
    pub(crate) fn for_request(
        &self,
        scope_id: &str,
        conversation_id: &str,
        parent_conversation_id: Option<&str>,
        now_ms: u64,
    ) -> Vec<String> {
        let conversations = lock(&self.conversations);
        let mut selected = Vec::new();
        for id in std::iter::once(conversation_id).chain(parent_conversation_id) {
            if let Some(conversation) = conversations
                .iter()
                .find(|c| c.scope_id == scope_id && c.conversation_id == id)
            {
                selected.extend(
                    conversation
                        .receipts
                        .iter()
                        .rev()
                        .filter(|(_, expires)| *expires > now_ms)
                        .map(|(receipt, _)| receipt.clone()),
                );
            }
        }
        selected.truncate(MAX_RECEIPTS);
        selected
    }
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn receipts_are_kept_per_conversation_and_include_the_parent() {
        let store = ReceiptStore::default();
        store.add("s", "parent", "p1".into(), 0);
        store.add("s", "child", "c1".into(), 0);
        store.add("other", "parent", "x".into(), 0);
        assert_eq!(
            store.for_request("s", "child", Some("parent"), 1),
            ["c1", "p1"]
        );
        assert_eq!(store.for_request("s", "child", None, 1), ["c1"]);
        assert_eq!(store.for_request("s", "parent", None, 1), ["p1"]);
        assert!(store.for_request("s", "unknown", None, 1).is_empty());
    }

    #[test]
    fn receipts_expire_and_are_bounded() {
        let store = ReceiptStore::default();
        for i in 0..=MAX_RECEIPTS {
            store.add("s", "c", format!("r{i}"), 0);
        }
        let kept = store.for_request("s", "c", None, 1);
        assert_eq!(kept.len(), MAX_RECEIPTS);
        assert_eq!(kept[0], format!("r{MAX_RECEIPTS}"), "the newest first");
        assert!(!kept.contains(&"r0".to_string()), "the oldest is dropped");
        assert!(store.for_request("s", "c", None, RECEIPT_TTL_MS).is_empty());
    }
}
