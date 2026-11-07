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

//! HTTP-framework observations. Polling a frame does not prove a socket write or receipt.
use axum::body::{Body, Bytes};
use http_body::{Body as HttpBody, Frame, SizeHint};
use pc_gateway_core::ExecutionObservation;
use pc_gateway_evidence::{DeliveryBoundary, DeliveryEvidence, EvidenceSink};
use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};

/// Offset in the exact outgoing body; this metadata contains no response content.
#[derive(Clone, Copy)]
pub(crate) struct FirstContentOffset(pub(crate) Option<usize>);

pub(crate) struct EvidenceBody {
    body: Body,
    observation: ExecutionObservation,
    sink: Arc<dyn EvidenceSink>,
    first: bool,
    first_content: Option<usize>,
    body_bytes: usize,
    completed: bool,
}

impl EvidenceBody {
    pub(crate) fn wrap(
        body: Body,
        observation: ExecutionObservation,
        sink: Arc<dyn EvidenceSink>,
        first_content: Option<usize>,
    ) -> Body {
        let wrapper = Self {
            body,
            observation,
            sink,
            first: false,
            first_content,
            body_bytes: 0,
            completed: false,
        };
        wrapper.record(DeliveryBoundary::FrameworkHandoff);
        Body::new(wrapper)
    }

    fn record(&self, boundary: DeliveryBoundary) {
        self.sink.record_delivery(&DeliveryEvidence {
            call_id: self.observation.call_id().to_owned(),
            boundary,
            elapsed_ms: self.observation.elapsed_ms(),
        });
    }
}

impl HttpBody for EvidenceBody {
    type Data = Bytes;
    type Error = axum::Error;

    fn poll_frame(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
    ) -> Poll<Option<Result<Frame<Bytes>, Self::Error>>> {
        let result = Pin::new(&mut self.body).poll_frame(cx);
        if let Poll::Ready(frame) = &result {
            if let Some(data) = frame
                .as_ref()
                .and_then(|r| r.as_ref().ok())
                .and_then(Frame::data_ref)
            {
                if !self.first && !data.is_empty() {
                    self.first = true;
                    self.record(DeliveryBoundary::FirstBodyFrame);
                }
                self.body_bytes = self.body_bytes.saturating_add(data.len());
                if self
                    .first_content
                    .is_some_and(|offset| offset < self.body_bytes)
                {
                    self.first_content = None;
                    self.record(DeliveryBoundary::FirstModelContent);
                }
            }
            if !self.completed
                && (frame.is_none() || (matches!(frame, Some(Ok(_))) && self.body.is_end_stream()))
            {
                self.completed = true;
                self.record(DeliveryBoundary::BodyCompleted);
            }
        }
        result
    }
    fn is_end_stream(&self) -> bool {
        self.body.is_end_stream()
    }
    fn size_hint(&self) -> SizeHint {
        self.body.size_hint()
    }
}

impl Drop for EvidenceBody {
    fn drop(&mut self) {
        if !self.completed {
            self.record(DeliveryBoundary::BodyDropped);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use http_body_util::BodyExt;
    use pc_gateway_core::RequestControl;
    use pc_gateway_evidence::{BufferedEvidenceSink, EvidenceEvent};
    use pc_gateway_protocol::{first_model_content_offset, OutputProtocol};
    use std::collections::VecDeque;
    use std::convert::Infallible;

    struct Frames(VecDeque<Bytes>);
    impl HttpBody for Frames {
        type Data = Bytes;
        type Error = Infallible;

        fn poll_frame(
            mut self: Pin<&mut Self>,
            _: &mut Context<'_>,
        ) -> Poll<Option<Result<Frame<Bytes>, Infallible>>> {
            Poll::Ready(self.0.pop_front().map(|bytes| Ok(Frame::data(bytes))))
        }
    }

    fn boundaries(sink: &BufferedEvidenceSink) -> Vec<String> {
        sink.events()
            .into_iter()
            .filter_map(|event| match event {
                EvidenceEvent::Delivery(e) => Some(
                    serde_json::to_value(e.boundary)
                        .unwrap()
                        .as_str()
                        .unwrap()
                        .to_owned(),
                ),
                _ => None,
            })
            .collect()
    }

    fn wrap(bytes: Vec<Bytes>, offset: Option<usize>, sink: Arc<BufferedEvidenceSink>) -> Body {
        let control = RequestControl::new(
            tokio::time::Instant::now() + std::time::Duration::from_secs(30),
            None,
        );
        EvidenceBody::wrap(
            Body::new(Frames(bytes.into())),
            control.observation(),
            sink,
            offset,
        )
    }

    #[tokio::test]
    async fn content_observation_waits_for_matching_frame_at_every_wire_split() {
        for (wire, protocol, sse) in [
            ("\u{feff}data: {\"choices\":[{\"delta\":{\"content\":\"FIRST\"}}]}\r\n\r\ndata: {\"choices\":[{\"delta\":{\"content\":\"LATER\"}}]}\r\n\r\n", OutputProtocol::OpenAiChat, true),
            ("event: response.created\ndata: {\"type\":\"response.created\"}\n\nevent: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"秘密\"}\n\n", OutputProtocol::OpenAiResponses, true),
            (r#"{"choices":[{"message":{"content":"\u79d8密"}}]}"#, OutputProtocol::OpenAiChat, false),
            ("data: {\"choices\":[{\"delta\":{\"role\":\"assistant\"}}]}\n\ndata: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"function\":{\"arguments\":\"{}\"}}]}}]}\n\n", OutputProtocol::OpenAiChat, true),
            (r#"{"output":[{"type":"message","content":[{"type":"refusal","refusal":"no"}]}]}"#, OutputProtocol::OpenAiResponses, false),
        ] {
            let offset = first_model_content_offset(wire.as_bytes(), protocol, sse).unwrap();
            for split in 0..=wire.len() {
                let sink = Arc::new(BufferedEvidenceSink::new());
                let mut body = wrap(vec![Bytes::copy_from_slice(&wire.as_bytes()[..split]), Bytes::copy_from_slice(&wire.as_bytes()[split..])], Some(offset), sink.clone());
                let first = body.frame().await.unwrap().unwrap().into_data().unwrap();
                assert_eq!(boundaries(&sink).iter().any(|b| b == "first_model_content"), split > offset);
                let second = body.frame().await.unwrap().unwrap().into_data().unwrap();
                assert_eq!([first.as_ref(), second.as_ref()].concat(), wire.as_bytes());
                assert!(body.frame().await.is_none());
                drop(body);
                assert_eq!(boundaries(&sink), ["framework_handoff", "first_body_frame", "first_model_content", "body_completed"]);
            }
        }
    }

    #[tokio::test]
    async fn dropping_after_control_frame_does_not_claim_later_content() {
        let wire = "event: response.created\ndata: {\"type\":\"response.created\"}\n\nevent: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"secret\"}\n\n";
        let split = wire.find("event: response.output_text").unwrap();
        let sink = Arc::new(BufferedEvidenceSink::new());
        let mut body = wrap(
            vec![
                Bytes::copy_from_slice(&wire.as_bytes()[..split]),
                Bytes::copy_from_slice(&wire.as_bytes()[split..]),
            ],
            first_model_content_offset(wire.as_bytes(), OutputProtocol::OpenAiResponses, true),
            sink.clone(),
        );
        body.frame().await.unwrap().unwrap();
        drop(body);
        assert_eq!(
            boundaries(&sink),
            ["framework_handoff", "first_body_frame", "body_dropped"]
        );
    }

    #[tokio::test]
    async fn control_only_output_completes_without_inventing_content() {
        let wire = b"event: response.created\ndata: {\"type\":\"response.created\"}\n\n";
        let sink = Arc::new(BufferedEvidenceSink::new());
        let body = wrap(
            vec![Bytes::from_static(wire)],
            first_model_content_offset(wire, OutputProtocol::OpenAiResponses, true),
            sink.clone(),
        );
        assert_eq!(body.collect().await.unwrap().to_bytes(), wire.as_slice());
        assert_eq!(
            boundaries(&sink),
            ["framework_handoff", "first_body_frame", "body_completed"]
        );
    }
}
