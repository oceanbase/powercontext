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

pub(crate) struct EvidenceBody {
    body: Body,
    observation: ExecutionObservation,
    sink: Arc<dyn EvidenceSink>,
    first: bool,
    completed: bool,
}

impl EvidenceBody {
    pub(crate) fn wrap(
        body: Body,
        observation: ExecutionObservation,
        sink: Arc<dyn EvidenceSink>,
    ) -> Body {
        let wrapper = Self {
            body,
            observation,
            sink,
            first: false,
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
            if !self.first
                && frame
                    .as_ref()
                    .and_then(|r| r.as_ref().ok())
                    .and_then(Frame::data_ref)
                    .is_some_and(|data| !data.is_empty())
            {
                self.first = true;
                self.record(DeliveryBoundary::FirstBodyFrame);
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
