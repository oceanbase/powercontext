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

use serde_json::{json, Value};
use std::time::{SystemTime, UNIX_EPOCH};

/// Match Pydantic AI's Copilot completion metadata defaults before release checking.
/// Copilot omits fields that ordinary OpenAI clients validate as required.
pub fn chat_response(bytes: &[u8]) -> Option<Vec<u8>> {
    let mut response = crate::json::unambiguous_value(bytes)?;
    let choices = response.get_mut("choices")?.as_array_mut()?;
    for (index, choice) in choices.iter_mut().enumerate() {
        let choice = choice.as_object_mut()?;
        if choice.get("index").is_none_or(Value::is_null) {
            choice.insert("index".into(), json!(index));
        }
    }
    let response = response.as_object_mut()?;
    response.insert("object".into(), json!("chat.completion"));
    if response.get("created").is_none_or(Value::is_null) {
        response.insert(
            "created".into(),
            json!(SystemTime::now().duration_since(UNIX_EPOCH).ok()?.as_secs()),
        );
    }
    serde_json::to_vec(response).ok()
}
