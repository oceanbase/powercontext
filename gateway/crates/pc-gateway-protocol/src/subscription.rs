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

use crate::{
    CanonicalRequest, GatewayRenderer, OutputProtocol, RenderError, RenderedPayload, WireRenderer,
};
use serde_json::{json, Value};

#[derive(Clone)]
pub enum SubscriptionProtocol {
    Grok,
    Codex,
    Kimi,
    Gemini { project: String },
    Claude,
}

/// Provider adaptation occurs on every render, including prepare refresh and degraded rendering.
/// The executor therefore checks exactly the bytes that the HTTP transport sends.
pub struct SubscriptionRenderer {
    pub inner: GatewayRenderer,
    pub subscription: Option<SubscriptionProtocol>,
}

impl WireRenderer for SubscriptionRenderer {
    fn name(&self) -> &'static str {
        self.inner.name()
    }

    fn message_list_key(&self) -> &'static str {
        self.inner.message_list_key()
    }

    fn output_protocol(&self) -> OutputProtocol {
        self.inner.output_protocol()
    }

    fn render(
        &self,
        request: &CanonicalRequest,
        plan: Option<&Value>,
    ) -> Result<RenderedPayload, RenderError> {
        let payload = self.inner.render(request, plan)?;
        let Some(ref subscription) = self.subscription else {
            return Ok(payload);
        };
        let mut body: Value = serde_json::from_slice(&payload.bytes).map_err(|_| invalid())?;
        match subscription {
            SubscriptionProtocol::Codex => {
                crate::codex::adapt(&mut body, self.message_list_key())?;
            }
            SubscriptionProtocol::Grok => {
                if self.message_list_key() == "input" {
                    crate::grok::adapt(&mut body)?;
                }
            }
            SubscriptionProtocol::Kimi => {
                if self.message_list_key() == "input" {
                    body["stream"] = json!(true);
                    body["store"] = json!(false);
                }
            }
            SubscriptionProtocol::Gemini { project } => {
                if self.message_list_key() != "messages" {
                    return Err(invalid());
                }
                body = crate::gemini::codeassist_request(&body, project)?;
            }
            SubscriptionProtocol::Claude => {
                crate::claude::validate(&body, self.message_list_key())?;
            }
        }
        Ok(RenderedPayload {
            bytes: serde_json::to_vec(&body).map_err(|_| invalid())?.into(),
            content_type: payload.content_type,
        })
    }

    fn restore_response(
        &self,
        request: &CanonicalRequest,
        body: bytes::Bytes,
    ) -> Option<bytes::Bytes> {
        if matches!(self.subscription, Some(SubscriptionProtocol::Grok))
            && self.message_list_key() == "input"
        {
            crate::grok::restore_response(request, body)
        } else {
            Some(body)
        }
    }
}

fn invalid() -> RenderError {
    RenderError::InvalidRenderPlan {
        renderer: "subscription",
    }
}
