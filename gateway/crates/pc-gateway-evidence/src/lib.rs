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

//! 证据适配层。
//!
//! RFC 约束：归一化实际目标身份、usage 完整性、供应商缓存 read/write、
//! 失败尝试与延迟；分别报告上游首 token 与首次内容释放时间；
//! **绝不记录 query/prompt/响应正文/凭据**；执行身份不进指标标签。
//! 已知 / 估算 / 未知 / 对账用量分开记录，不可用标 unknown 不记零。

use serde::{Deserialize, Serialize};

/// usage 完整性：不把缺失当零。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum UsageCompleteness {
    Known,
    Estimated,
    Unknown,
    Reconciled,
}

/// 单次调用的无正文证据记录。
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CallEvidence {
    /// 实际发送目标身份（归一化后），不是客户端声称的目标。
    pub actual_target: Option<String>,
    pub completeness: Option<UsageCompleteness>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub cache_read_tokens: Option<u64>,
    pub cache_write_tokens: Option<u64>,
    pub attempts: u32,
    /// 上游首 token 延迟（毫秒）。
    pub upstream_ttft_ms: Option<u64>,
    /// 首次内容释放延迟（毫秒）。strict 模式下必然晚于 ttft。
    pub first_release_ms: Option<u64>,
    /// 失败原因类别（无正文）。
    pub failure_reason: Option<String>,
}

/// 证据下沉抽象。实现可以是空对象（观测可选），
/// 借鉴 TensorZero 的 disabled client 模式。
pub trait EvidenceSink: Send + Sync {
    fn record_attempt(&self, attempt: u32);
    fn record_not_sent(&self, attempt: u32, reason: &str);
    fn record_call(&self, evidence: &CallEvidence);
}

/// 空对象实现：观测关闭时使用。
#[derive(Debug, Default, Clone, Copy)]
pub struct NoopEvidenceSink;

impl EvidenceSink for NoopEvidenceSink {
    fn record_attempt(&self, _attempt: u32) {}
    fn record_not_sent(&self, _attempt: u32, _reason: &str) {}
    fn record_call(&self, _evidence: &CallEvidence) {}
}

/// 内存缓冲实现：测试与本地演示用；不落盘、不落正文。
#[derive(Debug, Default)]
pub struct BufferedEvidenceSink {
    attempts: std::sync::Mutex<Vec<u32>>,
    not_sent: std::sync::Mutex<Vec<(u32, String)>>,
    calls: std::sync::Mutex<Vec<CallEvidence>>,
}

impl BufferedEvidenceSink {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn attempts(&self) -> Vec<u32> {
        self.attempts.lock().expect("evidence lock").clone()
    }

    pub fn not_sent(&self) -> Vec<(u32, String)> {
        self.not_sent.lock().expect("evidence lock").clone()
    }

    pub fn calls(&self) -> Vec<CallEvidence> {
        self.calls.lock().expect("evidence lock").clone()
    }
}

impl EvidenceSink for BufferedEvidenceSink {
    fn record_attempt(&self, attempt: u32) {
        self.attempts.lock().expect("evidence lock").push(attempt);
    }

    fn record_not_sent(&self, attempt: u32, reason: &str) {
        self.not_sent
            .lock()
            .expect("evidence lock")
            .push((attempt, reason.to_string()));
    }

    fn record_call(&self, evidence: &CallEvidence) {
        self.calls
            .lock()
            .expect("evidence lock")
            .push(evidence.clone());
    }
}
