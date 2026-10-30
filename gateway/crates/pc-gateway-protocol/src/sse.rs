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

//! SSE 解析。
//!
//! RFC 约束：strict 模式在完整 output/check 之后才组帧，
//! 因此网关 v1 对上游流是全量缓冲单元，不做增量放行；
//! bounded streaming 是后续独立提案。

use bytes::Bytes;

/// 缓冲中的一个原始事件（不解释语义；语义归 output/check 与调用方）。
#[derive(Debug, Clone)]
pub struct RawSseEvent {
    pub event: Option<String>,
    pub data: Bytes,
}

/// 增量解析器：从上游字节流切出原始 SSE 事件。
/// 只做切帧，不做内容检查——检查的是完整单元。
#[derive(Default)]
pub struct SseFramer {
    buf: Vec<u8>,
}

impl SseFramer {
    pub fn push(&mut self, chunk: &[u8]) {
        self.buf.extend_from_slice(chunk);
    }

    /// 取出所有完整事件（以空行分隔）。
    pub fn take_events(&mut self) -> Vec<RawSseEvent> {
        let mut events = Vec::new();
        while let Some(pos) = find_blank_line(&self.buf) {
            let block: Vec<u8> = self.buf.drain(..pos).collect();
            self.buf.drain(..2); // CRLF/LF 分隔符
            if let Some(ev) = parse_block(&block) {
                events.push(ev);
            }
        }
        events
    }

    /// 结束流时收尾（缓冲中残余的最后一个块）。
    pub fn finish(&mut self) -> Vec<RawSseEvent> {
        let rest = std::mem::take(&mut self.buf);
        match parse_block(&rest) {
            Some(ev) => vec![ev],
            None => Vec::new(),
        }
    }
}

fn find_blank_line(buf: &[u8]) -> Option<usize> {
    buf.windows(2).position(|w| w == b"\n\n" || w == b"\r\n")
}

fn parse_block(block: &[u8]) -> Option<RawSseEvent> {
    let text = String::from_utf8_lossy(block);
    let mut event = None;
    let mut data_lines = Vec::new();
    for line in text.lines() {
        if let Some(v) = line.strip_prefix("event:") {
            event = Some(v.trim().to_string());
        } else if let Some(v) = line.strip_prefix("data:") {
            data_lines.push(v.strip_prefix(' ').unwrap_or(v));
        }
    }
    if data_lines.is_empty() {
        return None;
    }
    Some(RawSseEvent {
        event,
        data: Bytes::from(data_lines.join("\n")),
    })
}
