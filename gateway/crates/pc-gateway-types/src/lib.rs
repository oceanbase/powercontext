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

//! PR #2264 契约值类型。
//!
//! 网关无策略：这里的类型只承载 Core 的输入/输出，
//! 与 openapi/powercontext.yaml 对齐。

mod check;
mod prepare;

pub use check::*;
pub use prepare::*;
