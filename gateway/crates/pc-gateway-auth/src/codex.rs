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
    http::{self, bearer, checked_header},
    store::{read_store, text},
    AuthError,
};
use serde_json::Value;
use std::path::Path;

pub(super) const RESPONSES_TARGET: &str = "https://chatgpt.com/backend-api/codex/responses";

pub(super) fn validate_target(target: &str) -> Result<(), AuthError> {
    http::validate_target(target, &[RESPONSES_TARGET])
}

// The official CLI owns renewal; reread its account and access token without writing it.
pub(super) fn headers(path: &Path) -> Result<Vec<(String, String)>, AuthError> {
    let data = read_store(path)?;
    if data
        .get("auth_mode")
        .and_then(Value::as_str)
        .is_some_and(|v| v != "chatgpt")
    {
        return Err(AuthError::Credentials);
    }
    let token = text(&data["tokens"], "access_token")?;
    let account = text(&data["tokens"], "account_id")?;
    Ok(vec![
        bearer(token)?,
        checked_header("chatgpt-account-id", account)?,
    ])
}
