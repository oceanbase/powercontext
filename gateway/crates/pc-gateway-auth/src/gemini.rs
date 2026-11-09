// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use super::*;

impl SubscriptionAuth {
    /// Existing, onboarded Gemini CLI account. The CLI retains renewal and onboarding ownership.
    /// Resolve a project with the same token used for inference; never combine two file versions.
    pub async fn gemini_context(
        &self,
        target: &str,
        now: i64,
    ) -> Result<(Vec<(String, String)>, String), AuthError> {
        if self.kind != Subscription::Gemini {
            return Err(AuthError::Configuration);
        }
        self.kind.validate_target(target)?;
        let data = read_store(&self.path)?;
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
        let response = self
            .client
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
