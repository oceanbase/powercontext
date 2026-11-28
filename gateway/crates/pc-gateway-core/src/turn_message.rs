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

pub(crate) use pc_gateway_protocol::{is_reminder_only, is_text_part, text_part};
use pc_gateway_protocol::{CanonicalMessage, CanonicalRequest, Role};

/// The current prompt is the latest user message other than a standalone harness reminder.
pub(crate) fn last_user_index(request: &CanonicalRequest) -> Option<usize> {
    request.messages.iter().rposition(is_user_prompt)
}

pub(crate) fn is_user_prompt(message: &CanonicalMessage) -> bool {
    message.role == Role::User && !is_reminder_only(&message.content)
}
