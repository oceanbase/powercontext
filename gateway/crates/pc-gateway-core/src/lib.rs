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

//! 执行器核心：一次托管调用的状态机。
//!
//! 对应 RFC「一次托管调用」时序图（PR #2199 两阶段检查形态）：
//!
//! ```text
//! 宿主完整输入 ─► check(before_send) ─┬─ allow ─► 发送 ─► 原始响应 ─► check(before_release) ─┬─ release ─► 返回
//!                                     ├─ withhold(渲染计划) ─► 只渲染获准字段 ─► 复检（最多一次）
//!                                     └─ block ──────────────────────────────────────────┴─ refuse ──► 显式拒绝
//! ```
//!
//! 网关无策略：每一步的裁决都来自 `GateClient`（check API 或嵌入式等价物），
//! 本 crate 只负责把裁决串成正确的控制流，并守住失败语义。

mod executor;
mod gate_client;
mod http_gate_client;
mod uds_gate_client;

pub use executor::*;
pub use gate_client::*;
pub use http_gate_client::*;
pub use uds_gate_client::*;
