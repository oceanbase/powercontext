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

//! 协议适配层：内部规范类型 ↔ 各服务商线格式。
//!
//! RFC 约束：只渲染获准的协议字段；保持角色、工具顺序、内容块、
//! 可复用边界与获准原生缓存设置；不向稳定 prompt 写入请求 ID/时间戳、
//! 不任意合并旧消息、不静默重映射目标。

mod canonical;
mod render;
mod sse;

pub use canonical::*;
pub use render::*;
pub use sse::*;
