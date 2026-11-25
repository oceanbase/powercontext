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

//! 回合类型的进程内计数。
//!
//! 判定本身由 `pc-gateway-protocol` 的 `turn_kind` 完成；这里只累计执行器观察到的结果。
//! 生产宿主在服务停止时输出汇总日志（见 `main`）；计数只在进程内累计，不作为指标标签导出。

use pc_gateway_types::TurnKind;
use std::sync::atomic::{AtomicU64, Ordering};

/// 各回合类型的累计判定次数与跳过准备次数。
#[derive(Default)]
pub(crate) struct TurnCounters {
    user_turn: AtomicU64,
    tool_continuation: AtomicU64,
    assistant_only: AtomicU64,
    empty: AtomicU64,
    skipped_prepare: AtomicU64,
}

/// 进程内累计快照；随进程重启归零。
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct TurnCounterSnapshot {
    pub user_turn: u64,
    pub tool_continuation: u64,
    pub assistant_only: u64,
    pub empty: u64,
    /// 非用户回合且没有可复用结果时跳过准备的次数。
    pub skipped_prepare: u64,
}

impl TurnCounters {
    pub(crate) fn record(&self, kind: TurnKind) {
        let counter = match kind {
            TurnKind::UserTurn => &self.user_turn,
            TurnKind::ToolContinuation => &self.tool_continuation,
            TurnKind::AssistantOnly => &self.assistant_only,
            TurnKind::Empty => &self.empty,
        };
        counter.fetch_add(1, Ordering::Relaxed);
    }

    pub(crate) fn record_skipped_prepare(&self) {
        self.skipped_prepare.fetch_add(1, Ordering::Relaxed);
    }

    pub(crate) fn snapshot(&self) -> TurnCounterSnapshot {
        TurnCounterSnapshot {
            user_turn: self.user_turn.load(Ordering::Relaxed),
            tool_continuation: self.tool_continuation.load(Ordering::Relaxed),
            assistant_only: self.assistant_only.load(Ordering::Relaxed),
            empty: self.empty.load(Ordering::Relaxed),
            skipped_prepare: self.skipped_prepare.load(Ordering::Relaxed),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counters_accumulate_each_kind_and_skipped_prepares() {
        let counters = TurnCounters::default();
        counters.record(TurnKind::UserTurn);
        counters.record(TurnKind::ToolContinuation);
        counters.record(TurnKind::ToolContinuation);
        counters.record(TurnKind::AssistantOnly);
        counters.record(TurnKind::Empty);
        counters.record_skipped_prepare();
        assert_eq!(
            counters.snapshot(),
            TurnCounterSnapshot {
                user_turn: 1,
                tool_continuation: 2,
                assistant_only: 1,
                empty: 1,
                skipped_prepare: 1,
            }
        );
    }
}
