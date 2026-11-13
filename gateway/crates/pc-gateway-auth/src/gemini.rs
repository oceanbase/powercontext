// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::{
    http::{self, bearer},
    store::{read_store, text, STORE_LIMIT},
    AuthError,
};
use serde_json::Value;
use std::path::Path;

pub(super) const CHAT_TARGET: &str =
    "https://cloudcode-pa.googleapis.com/v1internal:generateContent";

pub(super) fn validate_target(target: &str) -> Result<(), AuthError> {
    http::validate_target(target, &[CHAT_TARGET])
}

pub(super) struct Gemini {
    pub(super) codeassist_url: String,
    pub(super) codeassist_project: Option<String>,
}

impl Gemini {
    pub(super) fn new() -> Self {
        Self {
            codeassist_url: "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist".into(),
            codeassist_project: std::env::var("GOOGLE_CLOUD_PROJECT")
                .or_else(|_| std::env::var("GOOGLE_CLOUD_PROJECT_ID"))
                .ok(),
        }
    }

    // Resolve a project with the same read-only CLI token used for inference.
    // Renewal and onboarding remain owned by the CLI.
    pub(super) async fn context(
        &self,
        path: &Path,
        client: &reqwest::Client,
        now: i64,
    ) -> Result<(Vec<(String, String)>, String), AuthError> {
        let data = read_store(path)?;
        let token = text(&data, "access_token")?;
        if data
            .get("type")
            .and_then(Value::as_str)
            .is_some_and(|v| v != "authorized_user")
            || data["expiry_date"]
                .as_i64()
                .filter(|ms| *ms > now.saturating_mul(1000))
                .is_none()
        {
            return Err(AuthError::Credentials);
        }
        let headers = vec![
            bearer(token)?,
            (
                "user-agent".into(),
                concat!("PowerContextGateway/", env!("CARGO_PKG_VERSION")).into(),
            ),
        ];
        let response = client
            .post(&self.codeassist_url)
            .header("authorization", &headers[0].1)
            .json(&serde_json::json!({
                "cloudaicompanionProject":self.codeassist_project,
                "metadata":{"duetProject":self.codeassist_project},
            }))
            .send()
            .await
            .map_err(|_| AuthError::Credentials)?;
        if !response.status().is_success() {
            return Err(AuthError::Credentials);
        }
        let mut response = response;
        let mut bytes = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(|_| AuthError::Credentials)? {
            if chunk.len() > (STORE_LIMIT as usize).saturating_sub(bytes.len()) {
                return Err(AuthError::Credentials);
            }
            bytes.extend_from_slice(&chunk);
        }
        let account: Value = serde_json::from_slice(&bytes).map_err(|_| AuthError::Credentials)?;
        let project = account["cloudaicompanionProject"]
            .as_str()
            .or(self.codeassist_project.as_deref())
            .filter(|v| !v.is_empty())
            .ok_or(AuthError::Credentials)?;
        if !["currentTier", "paidTier"]
            .iter()
            .any(|key| account[*key].is_object())
        {
            return Err(AuthError::Credentials);
        }
        Ok((headers, project.into()))
    }
}
