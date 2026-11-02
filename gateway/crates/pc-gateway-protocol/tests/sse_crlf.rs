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

//! P2 review：CRLF 分隔的 SSE 事件不得被拆成多个碎片。

use pc_gateway_protocol::SseFramer;

#[test]
fn crlf_events_not_split_into_fragments() {
    let mut framer = SseFramer::default();
    // 一个命名事件，CRLF 行终止，以 \r\n\r\n 结束
    framer.push(b"event: delta\r\ndata: first\r\ndata: second\r\n\r\n");
    let events = framer.take_events();
    assert_eq!(
        events.len(),
        1,
        "CRLF 事件应保持为一个事件，实际被拆成 {} 个",
        events.len()
    );
    assert_eq!(events[0].event.as_deref(), Some("delta"));
    assert_eq!(
        events[0].data.as_ref(),
        &bytes::Bytes::from_static(b"first\nsecond")
    );
}

#[test]
fn lf_events_still_work() {
    let mut framer = SseFramer::default();
    framer.push(b"event: delta\ndata: first\ndata: second\n\n");
    let events = framer.take_events();
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].event.as_deref(), Some("delta"));
    assert_eq!(
        events[0].data.as_ref(),
        &bytes::Bytes::from_static(b"first\nsecond")
    );
}

#[test]
fn mixed_crlf_events() {
    let mut framer = SseFramer::default();
    framer.push(b"event: a\r\ndata: one\r\n\r\nevent: b\r\ndata: two\r\n\r\n");
    let events = framer.take_events();
    assert_eq!(events.len(), 2, "两个 CRLF 事件");
    assert_eq!(events[0].event.as_deref(), Some("a"));
    assert_eq!(events[1].event.as_deref(), Some("b"));
}
