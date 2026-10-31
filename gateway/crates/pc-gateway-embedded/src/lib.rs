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

//! 嵌入式路径的占位 crate。
//!
//! RFC：SDK/内置执行与 Rust GatewayRuntime 是按调用配置的两种执行路径，
//! 同一部署可并存，落实同一套 Core 决策。本 crate 未来承载
//! pyo3/FFI 绑定，让 Python 进程内执行器复用 `pc-gateway-core` 的状态机，
//! 并以进程内 `GateClient` 实现直调 Core 算法（无需 HTTP）。
//!
//! v1 尚未实现；保留 crate 以固定 workspace 边界。
