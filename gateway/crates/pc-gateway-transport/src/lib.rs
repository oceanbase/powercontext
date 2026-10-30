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

//! 传输层：发送获准字节、取消、归属明确的有界重试。
//!
//! RFC 失败语义：
//! - 准入后正文/目标/设置变化 → 重新准入后才能发送；
//! - 确定性未送达 → 唯一所有者在有效准入和有界次数内重试；
//! - 可能已送达 → 返回不确定，不自动付费重发；
//! - 未 admission 的字节绝不出网。

mod reqwest_transport;
mod retry;
mod sender;

pub use reqwest_transport::*;
pub use retry::*;
pub use sender::*;
