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

use pc_gateway_protocol::{first_model_content_offset, OutputProtocol};

fn assert_at(body: &str, protocol: OutputProtocol, sse: bool, content: &str) {
    let offset = first_model_content_offset(body.as_bytes(), protocol, sse)
        .expect("model content must be recognized");
    assert!(
        body[offset..].starts_with(content),
        "offset {offset} does not locate {content}"
    );
}

#[test]
fn json_model_content_ignores_metadata_and_preserves_wire_offsets() {
    assert_at(
        r#"{"metadata":{"content":"DECOY"}, "choices":[{"message":{"role":"assistant","content":"你好\\nactual"}}]}"#,
        OutputProtocol::OpenAiChat,
        false,
        "你好",
    );
    assert_at(
        r#"{"choices":[{"message":{"content":null,"tool_calls":[{"function":{"arguments":"{\\\"x\\\":1}","name":"tool"}}]}}]}"#,
        OutputProtocol::OpenAiChat,
        false,
        "{",
    );
    assert_at(
        r#"{"choices":[{"message":{"content":"","refusal":"refused"}}]}"#,
        OutputProtocol::OpenAiChat,
        false,
        "refused",
    );
    assert_at(
        r#"{"output":[{"type":"reasoning","summary":[{"text":"DECOY"}]},{"type":"message","content":[{"type":"output_text","text":"actual"}]}]}"#,
        OutputProtocol::OpenAiResponses,
        false,
        "actual",
    );
    assert_at(
        r#"{"output":[{"type":"function_call","arguments":"{}","name":"tool"}]}"#,
        OutputProtocol::OpenAiResponses,
        false,
        "{",
    );
    assert_at(
        r#"{"output":[{"type":"message","content":[{"type":"refusal","refusal":"refused"}]}]}"#,
        OutputProtocol::OpenAiResponses,
        false,
        "refused",
    );
    assert_at(
        r#"{"output":[{"type":"message","content":[{"type":"output_text","text":"\u4f60\u597d"}]}]}"#,
        OutputProtocol::OpenAiResponses,
        false,
        r"\u4f60",
    );
}

#[test]
fn empty_error_and_unsupported_output_do_not_invent_a_content_boundary() {
    for (protocol, body) in [
        (
            OutputProtocol::OpenAiChat,
            r#"{"error":{"message":"DECOY"}}"#,
        ),
        (
            OutputProtocol::OpenAiChat,
            r#"{"choices":[{"message":{"role":"user","content":"DECOY"}}]}"#,
        ),
        (
            OutputProtocol::OpenAiChat,
            r#"{"choices":[{"message":{"content":"","tool_calls":[]}}],"usage":{"content":"DECOY"}}"#,
        ),
        (
            OutputProtocol::OpenAiResponses,
            r#"{"output":[{"type":"message","content":[{"type":"image","text":"DECOY"}]}]}"#,
        ),
        (
            OutputProtocol::OpenAiResponses,
            r#"{"output":[],"text":"DECOY"}"#,
        ),
        (
            OutputProtocol::OpenAiResponses,
            r#"{"output":[{"type":"message","content":[{"type":"output_text","text":""}]}]}"#,
        ),
        (
            OutputProtocol::OpenAiResponses,
            r#"{"output":[{"type":"message","role":"user","content":[{"type":"output_text","text":"DECOY"}]}]}"#,
        ),
        (OutputProtocol::OpenAiChat, "malformed"),
    ] {
        assert_eq!(
            first_model_content_offset(body.as_bytes(), protocol, false),
            None,
            "{body}"
        );
    }
}

#[test]
fn chat_sse_skips_roles_empty_deltas_usage_and_done_before_content() {
    let body = "data: {\"choices\":[{\"delta\":{\"role\":\"assistant\",\"content\":\"\"}}]}\r\n\r\ndata: {\"usage\":{\"text\":\"DECOY\"},\"choices\":[]}\r\n\r\ndata: {\"choices\":[{\"delta\":{\"content\":\"actual\"}}]}\r\n\r\ndata: [DONE]\r\n\r\n";
    assert_at(body, OutputProtocol::OpenAiChat, true, "actual");
    let tool = "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"function\":{\"arguments\":\"{\"}}]}}]}\n\n";
    assert_at(tool, OutputProtocol::OpenAiChat, true, "{");
}

#[test]
fn responses_sse_locates_content_after_controls_across_multiline_data() {
    let body = ": comment\r\nevent: response.created\r\ndata: {\"type\":\"response.created\",\"delta\":\"DECOY\"}\r\n\r\nevent: response.output_text.delta\r\ndata: {\"type\":\"response.output_text.delta\",\r\ndata: \"delta\":\"actual\"}\r\n\r\n";
    assert_at(body, OutputProtocol::OpenAiResponses, true, "actual");
    let refusal = "event: response.refusal.delta\ndata: {\"delta\":\"refused\"}\n\n";
    assert_at(refusal, OutputProtocol::OpenAiResponses, true, "refused");
    let tool = "data: {\"type\":\"response.function_call_arguments.delta\",\"delta\":\"{\"}\n\n";
    assert_at(tool, OutputProtocol::OpenAiResponses, true, "{");
    let item = "data: {\"type\":\"response.output_item.added\",\"item\":{\"type\":\"function_call\",\"name\":\"tool\",\"arguments\":\"\"}}\n\n";
    assert_at(item, OutputProtocol::OpenAiResponses, true, "tool");
}

#[test]
fn control_only_conflicting_and_unterminated_sse_remain_without_content() {
    for body in [
        "event: response.created\ndata: {\"type\":\"response.created\",\"delta\":\"DECOY\"}\n\n",
        "event: response.created\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"DECOY\"}\n\n",
        "data: {\"type\":\"response.output_text.delta\",\"delta\":\"\"}\n\n",
        "data: {\"type\":\"response.output_text.delta\",\"delta\":\"DECOY\"}\n",
        "data: {\"type\":\"response.failed\",\"error\":{\"message\":\"DECOY\"}}\n\n",
    ] {
        assert_eq!(first_model_content_offset(body.as_bytes(), OutputProtocol::OpenAiResponses, true), None, "{body}");
    }
}

#[test]
fn large_content_needs_no_fixed_observer_buffer_or_content_copy() {
    let content = "x".repeat(256 * 1024);
    let body = format!(r#"{{"choices":[{{"message":{{"content":"{content}"}}}}]}}"#);
    let offset =
        first_model_content_offset(body.as_bytes(), OutputProtocol::OpenAiChat, false).unwrap();
    assert_eq!(&body[offset..offset + content.len()], content);
}

#[test]
fn sse_ignores_only_one_leading_bom_and_returns_original_wire_offset() {
    for (wire, protocol, content) in [
        ("\u{feff}data: {\"choices\":[{\"delta\":{\"content\":\"FIRST\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"content\":\"LATER\"}}]}\n\n", OutputProtocol::OpenAiChat, "FIRST"),
        ("\u{feff}event: response.output_text.delta\r\ndata: {\"delta\":\"FIRST\"}\r\n\r\n", OutputProtocol::OpenAiResponses, "FIRST"),
        ("\u{feff}data: {\"type\":\"response.output_text.delta\",\r\ndata: \"delta\":\"FIRST\"}\r\n\r\n", OutputProtocol::OpenAiResponses, "FIRST"),
        ("\u{feff}data: {\"choices\":[{\"delta\":{\"content\":\"\u{feff}FIRST\"}}]}\n\n", OutputProtocol::OpenAiChat, "\u{feff}FIRST"),
    ] {
        assert_eq!(first_model_content_offset(wire.as_bytes(), protocol, true), wire.find(content));
    }
    for wire in [
        "\u{feff}\u{feff}data: {\"choices\":[{\"delta\":{\"content\":\"DECOY\"}}]}\n\n",
        ": comment\n\u{feff}data: {\"choices\":[{\"delta\":{\"content\":\"DECOY\"}}]}\n\n",
    ] {
        assert_eq!(
            first_model_content_offset(wire.as_bytes(), OutputProtocol::OpenAiChat, true),
            None
        );
    }
}
