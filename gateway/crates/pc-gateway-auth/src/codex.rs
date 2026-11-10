// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

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
