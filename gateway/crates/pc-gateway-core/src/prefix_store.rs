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

//! 记忆抽取复用的会话前缀（#2413）。
//!
//! 每个用户回合的终止响应放行后，网关在内存中保留这次发给上游的原始请求和响应输出，用一个随机句柄引用。
//! Core 抽取记忆时凭句柄请网关在这段前缀后追加抽取指令再发送，这样请求几乎全部命中服务商为 Agent 写下的
//! 前缀缓存。前缀只是临时的缓存线索：只在网关内存里，有上限，按时间过期；从不进入 Source 或证据。

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

/// 保留的前缀数上限：超出后淘汰最早的。
const MAX_PREFIXES: usize = 64;
/// 保留的前缀总字节数上限：超出后淘汰最早的。一个前缀是整个请求体，只受网关的请求体上限约束，所以条数之外
/// 还要按字节封顶。
const MAX_PREFIX_BYTES: usize = 64 * 1024 * 1024;
/// 前缀保留时长：Core 的后台抽取通常在回合结束后几分钟内运行，服务商缓存也只保留这么久。
pub const PREFIX_TTL_MS: u64 = 30 * 60 * 1000;

/// 一个用户回合的前缀。
#[derive(Clone, Debug)]
pub struct PrefixEntry {
    pub scope_id: String,
    pub target_profile: String,
    pub conversation_id: String,
    /// 发给上游的原始请求体（终止响应对应的那次请求）。
    pub request_body: bytes::Bytes,
    /// 终止响应的 output 条目。
    pub response_output: Vec<serde_json::Value>,
    /// 原请求里注入块的绑定与位置。抽取请求原样沿用，前缀里的注入块才能凭原绑定通过检查。
    pub prepared_contexts: Vec<pc_gateway_types::PreparedContextLocation>,
}

impl PrefixEntry {
    /// 请求体与响应输出的字节数。
    fn bytes(&self) -> usize {
        self.request_body.len()
            + self
                .response_output
                .iter()
                .map(|item| item.to_string().len())
                .sum::<usize>()
    }
}

struct Kept {
    handle: String,
    entry: Arc<PrefixEntry>,
    bytes: usize,
    expires_ms: u64,
}

#[derive(Default)]
pub struct PrefixStore {
    entries: Mutex<VecDeque<Kept>>,
}

impl PrefixStore {
    /// 保存一个前缀，返回 128 位随机句柄。取不到随机数，或这一个前缀就超过总字节数上限时不保存：没有句柄的
    /// 回合走普通抽取。
    pub fn add(&self, entry: PrefixEntry, now_ms: u64) -> Option<String> {
        let bytes = entry.bytes();
        if bytes > MAX_PREFIX_BYTES {
            return None;
        }
        let mut random = [0u8; 16];
        getrandom::fill(&mut random).ok()?;
        let handle: String = random.iter().map(|b| format!("{b:02x}")).collect();
        let mut entries = lock(&self.entries);
        entries.retain(|kept| kept.expires_ms > now_ms);
        let mut total: usize = entries.iter().map(|kept| kept.bytes).sum();
        while entries.len() >= MAX_PREFIXES || total + bytes > MAX_PREFIX_BYTES {
            match entries.pop_front() {
                Some(evicted) => total -= evicted.bytes,
                None => break,
            }
        }
        entries.push_back(Kept {
            handle: handle.clone(),
            entry: Arc::new(entry),
            bytes,
            expires_ms: now_ms + PREFIX_TTL_MS,
        });
        Some(handle)
    }

    /// 句柄对应的前缀；句柄未知、已过期或属于其他 Scope 时为 `None`。
    pub fn get(&self, handle: &str, scope_id: &str, now_ms: u64) -> Option<Arc<PrefixEntry>> {
        lock(&self.entries)
            .iter()
            .find(|kept| {
                kept.handle == handle && kept.entry.scope_id == scope_id && kept.expires_ms > now_ms
            })
            .map(|kept| kept.entry.clone())
    }
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(scope: &str) -> PrefixEntry {
        PrefixEntry {
            scope_id: scope.into(),
            target_profile: "codex".into(),
            conversation_id: "codex-thread:a".into(),
            request_body: bytes::Bytes::from_static(b"{}"),
            response_output: Vec::new(),
            prepared_contexts: Vec::new(),
        }
    }

    #[test]
    fn handles_are_random_scoped_and_expire() {
        let store = PrefixStore::default();
        let first = store.add(entry("s"), 0).unwrap();
        let second = store.add(entry("s"), 0).unwrap();
        assert_ne!(first, second);
        assert_eq!(first.len(), 32);
        assert!(store.get(&first, "s", 1).is_some());
        assert!(
            store.get(&first, "other", 1).is_none(),
            "another Scope cannot use it"
        );
        assert!(store.get("unknown", "s", 1).is_none());
        assert!(store.get(&first, "s", PREFIX_TTL_MS).is_none(), "expired");
    }

    #[test]
    fn the_store_is_bounded() {
        let store = PrefixStore::default();
        let oldest = store.add(entry("s"), 0).unwrap();
        for _ in 0..MAX_PREFIXES {
            store.add(entry("s"), 0);
        }
        assert!(store.get(&oldest, "s", 1).is_none());
    }

    #[test]
    fn the_store_is_bounded_in_bytes() {
        let sized = |bytes: usize| PrefixEntry {
            request_body: bytes::Bytes::from(vec![b'x'; bytes]),
            ..entry("s")
        };
        let store = PrefixStore::default();
        let first = store.add(sized(MAX_PREFIX_BYTES / 2), 0).unwrap();
        let second = store.add(sized(MAX_PREFIX_BYTES / 2), 0).unwrap();
        assert!(store.get(&first, "s", 1).is_some(), "both fit the limit");
        let third = store.add(sized(MAX_PREFIX_BYTES / 2), 0).unwrap();
        assert!(
            store.get(&first, "s", 1).is_none(),
            "the oldest is evicted for room"
        );
        assert!(store.get(&second, "s", 1).is_some());
        assert!(store.get(&third, "s", 1).is_some());
        assert!(
            store.add(sized(MAX_PREFIX_BYTES + 1), 0).is_none(),
            "a prefix over the limit is not kept"
        );
        assert!(store.get(&third, "s", 1).is_some(), "and evicts nothing");
    }
}
