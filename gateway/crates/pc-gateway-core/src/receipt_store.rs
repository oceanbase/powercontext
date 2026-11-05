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
//! 会话）的请求重放这些密文时，网关把回执随 before_send 一起交给 Core 验证。before_send 放行时 Core 返回一张合并
//! 回执，带上这次转交的本会话回执中仍有效的全部证明；网关用它替换这些回执，所以活跃会话的回执数不随长度增长，
//! 重试或分叉的请求也不会丢失证明。
//! 回执由 Core 签名，网关只负责按会话保存和转交；状态在内存中，有上限，过期后丢弃。

use std::collections::VecDeque;
use std::sync::Mutex;

/// 每个会话保留的回执数，以及一次 before_send 最多转交的回执数（与 Core 的上限一致）。
pub(crate) const MAX_RECEIPTS: usize = 32;
/// 会话数上限：超出后淘汰最早写入的会话。
const MAX_CONVERSATIONS: usize = 1024;
/// 回执在网关保留的时长，与 Core 的有效期一致。
pub(crate) const RECEIPT_TTL_MS: u64 = 60 * 60 * 1000;

struct Receipt {
    id: u64,
    token: String,
    expires_ms: u64,
}

struct Conversation {
    scope_id: String,
    conversation_id: String,
    receipts: VecDeque<Receipt>,
}

#[derive(Default)]
struct Receipts {
    conversations: VecDeque<Conversation>,
    next_id: u64,
}

/// 一次 before_send 转交的回执，以及其中属于本会话的回执 ID（合并时只替换它们）。
#[derive(Debug, Default)]
pub(crate) struct Presented {
    pub(crate) receipts: Vec<String>,
    pub(crate) own: Vec<u64>,
}

#[derive(Default)]
pub(crate) struct ReceiptStore {
    inner: Mutex<Receipts>,
}

impl ReceiptStore {
    pub(crate) fn add(&self, scope_id: &str, conversation_id: &str, receipt: String, now_ms: u64) {
        self.store(scope_id, conversation_id, &[], receipt, now_ms);
    }

    /// 用 before_send 返回的合并回执替换这次转交的本会话回执。快照之后新加入的回执和父会话的回执不受影响。
    pub(crate) fn consolidate(
        &self,
        scope_id: &str,
        conversation_id: &str,
        replaced: &[u64],
        receipt: String,
        now_ms: u64,
    ) {
        self.store(scope_id, conversation_id, replaced, receipt, now_ms);
    }

    fn store(
        &self,
        scope_id: &str,
        conversation_id: &str,
        replaced: &[u64],
        receipt: String,
        now_ms: u64,
    ) {
        let mut inner = lock(&self.inner);
        inner.next_id += 1;
        let id = inner.next_id;
        let conversations = &mut inner.conversations;
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
            .retain(|r| r.expires_ms > now_ms && !replaced.contains(&r.id));
        if conversation.receipts.len() >= MAX_RECEIPTS {
            conversation.receipts.pop_front();
        }
        conversation.receipts.push_back(Receipt {
            id,
            token: receipt,
            expires_ms: now_ms + RECEIPT_TTL_MS,
        });
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
    ) -> Presented {
        let inner = lock(&self.inner);
        let mut selected = Vec::new();
        for (own, id) in std::iter::once((true, conversation_id))
            .chain(parent_conversation_id.map(|parent| (false, parent)))
        {
            if let Some(conversation) = inner
                .conversations
                .iter()
                .find(|c| c.scope_id == scope_id && c.conversation_id == id)
            {
                selected.extend(
                    conversation
                        .receipts
                        .iter()
                        .rev()
                        .filter(|r| r.expires_ms > now_ms)
                        .map(|r| (own, r.id, r.token.clone())),
                );
            }
        }
        selected.truncate(MAX_RECEIPTS);
        Presented {
            own: selected
                .iter()
                .filter(|(own, ..)| *own)
                .map(|(_, id, _)| *id)
                .collect(),
            receipts: selected.into_iter().map(|(.., token)| token).collect(),
        }
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
            store.for_request("s", "child", Some("parent"), 1).receipts,
            ["c1", "p1"]
        );
        assert_eq!(store.for_request("s", "child", None, 1).receipts, ["c1"]);
        assert_eq!(store.for_request("s", "parent", None, 1).receipts, ["p1"]);
        assert!(store
            .for_request("s", "unknown", None, 1)
            .receipts
            .is_empty());
    }

    #[test]
    fn receipts_expire_and_are_bounded() {
        let store = ReceiptStore::default();
        for i in 0..=MAX_RECEIPTS {
            store.add("s", "c", format!("r{i}"), 0);
        }
        let kept = store.for_request("s", "c", None, 1).receipts;
        assert_eq!(kept.len(), MAX_RECEIPTS);
        assert_eq!(kept[0], format!("r{MAX_RECEIPTS}"), "the newest first");
        assert!(!kept.contains(&"r0".to_string()), "the oldest is dropped");
        assert!(store
            .for_request("s", "c", None, RECEIPT_TTL_MS)
            .receipts
            .is_empty());
    }

    #[test]
    fn consolidation_replaces_only_the_presented_receipts_of_the_conversation() {
        let store = ReceiptStore::default();
        store.add("s", "parent", "p1".into(), 0);
        store.add("s", "child", "c1".into(), 0);
        store.add("s", "child", "c2".into(), 0);
        let presented = store.for_request("s", "child", Some("parent"), 1);
        assert_eq!(presented.receipts, ["c2", "c1", "p1"]);
        // A release that finishes before this check's consolidation keeps its receipt.
        store.add("s", "child", "c3".into(), 1);
        store.consolidate("s", "child", &presented.own, "merged".into(), 2);
        assert_eq!(
            store.for_request("s", "child", Some("parent"), 3).receipts,
            ["merged", "c3", "p1"]
        );
        assert_eq!(store.for_request("s", "parent", None, 3).receipts, ["p1"]);
    }

    #[test]
    fn an_active_conversation_keeps_a_bounded_number_of_receipts() {
        let store = ReceiptStore::default();
        for turn in 0..100 {
            let presented = store.for_request("s", "c", None, turn);
            store.consolidate("s", "c", &presented.own, format!("merged{turn}"), turn);
            store.add("s", "c", format!("release{turn}"), turn);
        }
        assert_eq!(
            store.for_request("s", "c", None, 100).receipts,
            ["release99", "merged99"]
        );
    }
}
