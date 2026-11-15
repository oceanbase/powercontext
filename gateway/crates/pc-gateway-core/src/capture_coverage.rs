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

//! Capture facts relative to a versioned projection; no authority or admission policy.

use serde_json::{json, Value};

#[derive(Default)]
pub(crate) struct CaptureLosses {
    pub field_byte_limit: bool,
    pub transcript_byte_limit: bool,
    pub unmeasured_projection: bool,
}

impl CaptureLosses {
    pub fn declaration(&self) -> Value {
        let mut reasons = Vec::new();
        if self.field_byte_limit {
            reasons.push("field_byte_limit");
        }
        if self.transcript_byte_limit {
            reasons.push("transcript_byte_limit");
        }
        if self.unmeasured_projection {
            reasons.push("unmeasured_projection");
        }
        declaration("gateway-turn-text.v1", &reasons)
    }
}

fn unit(projection: &str) -> Option<&'static str> {
    match projection {
        "hermes-turn-text.v1" => Some("user_assistant_text_pair"),
        "hermes-precompress-text.v1" => Some("new_user_assistant_window"),
        "gateway-turn-text.v1" => Some("gateway_turn_projection"),
        _ => None,
    }
}

fn declaration(projection: &str, reasons: &[&str]) -> Value {
    let mut reasons = reasons.to_vec();
    reasons.sort_unstable();
    reasons.dedup();
    let status = if reasons
        .iter()
        .any(|reason| *reason != "unmeasured_projection")
    {
        "partial"
    } else if reasons.is_empty() {
        "complete"
    } else {
        "unknown"
    };
    // Fixed fields and enums bound every supported v1 object below 512 UTF-8 bytes.
    json!({
        "schema": "powercontext.capture-coverage.v1",
        "projection": projection,
        "unit": unit(projection).expect("capture projection is supported"),
        "status": status,
        "reasons": reasons,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Coverage {
        schema: String,
        projection: String,
        unit: String,
        status: String,
        reasons: Vec<String>,
    }

    fn interpreted_status(metadata: &Value) -> &str {
        let value = &metadata["powercontext_capture_coverage"];
        let Ok(parsed) = serde_json::from_value::<Coverage>(value.clone()) else {
            return "unknown";
        };
        if parsed.schema != "powercontext.capture-coverage.v1"
            || unit(&parsed.projection) != Some(parsed.unit.as_str())
            || !parsed.reasons.iter().all(|reason| {
                matches!(
                    reason.as_str(),
                    "character_limit"
                        | "field_byte_limit"
                        | "transcript_byte_limit"
                        | "unmeasured_projection"
                )
            })
            || parsed.reasons.windows(2).any(|pair| pair[0] >= pair[1])
            || serde_json::to_vec(value).unwrap().len() > 512
        {
            return "unknown";
        }
        let reasons: Vec<&str> = parsed.reasons.iter().map(String::as_str).collect();
        if value != &declaration(&parsed.projection, &reasons) {
            return "unknown";
        }
        match parsed.status.as_str() {
            "complete" => "complete",
            "partial" => "partial",
            _ => "unknown",
        }
    }

    #[test]
    fn shared_capture_coverage_contract() {
        let fixture: Value = serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../tests/integration_conformance/capture_coverage_cases.json"
        )))
        .unwrap();
        for case in fixture["declarations"].as_array().unwrap() {
            let reasons: Vec<&str> = case["reasons"]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_str().unwrap())
                .collect();
            let actual = declaration(case["projection"].as_str().unwrap(), &reasons);
            assert_eq!(actual, case["expected"], "{}", case["id"]);
            assert!(serde_json::to_vec(&actual).unwrap().len() <= 512);
        }
        for case in fixture["interpretations"].as_array().unwrap() {
            assert_eq!(
                interpreted_status(&case["metadata"]),
                case["expected"].as_str().unwrap(),
                "{}",
                case["id"]
            );
        }
    }
}
