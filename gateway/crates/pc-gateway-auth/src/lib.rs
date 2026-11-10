// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

//! Subscription authentication. Credentials never enter model bodies or Core check payloads.

use serde::{Deserialize, Serialize};
use std::path::PathBuf;

mod claude;
mod codex;
mod copilot;
mod gemini;
mod grok;
mod http;
mod kimi;
mod managed;
mod oauth;
mod qwen;
mod store;

pub use grok::{GROK_CLIENT_ID, GROK_ISSUER, GROK_SCOPE};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Subscription {
    Grok,
    Codex,
    Copilot,
    Qwen,
    Kimi,
    Gemini,
    Claude,
}

impl Subscription {
    pub fn parse(value: &str) -> Result<Self, AuthError> {
        match value {
            "grok" => Ok(Self::Grok),
            "codex" => Ok(Self::Codex),
            "copilot" => Ok(Self::Copilot),
            "qwen" => Ok(Self::Qwen),
            "kimi" => Ok(Self::Kimi),
            "gemini" => Ok(Self::Gemini),
            "claude" => Ok(Self::Claude),
            _ => Err(AuthError::Configuration),
        }
    }

    /// Provider-owned defaults; empty values mean the route is unavailable.
    pub fn default_targets(self) -> (&'static str, &'static str) {
        match self {
            Self::Grok => (grok::CHAT_TARGET, grok::RESPONSES_TARGET),
            Self::Codex => ("", codex::RESPONSES_TARGET),
            Self::Copilot => (copilot::CHAT_TARGET, copilot::RESPONSES_TARGET),
            Self::Qwen => (qwen::CHAT_TARGET, qwen::RESPONSES_TARGET),
            Self::Kimi => (kimi::CHAT_TARGET, kimi::RESPONSES_TARGET),
            Self::Gemini => (gemini::CHAT_TARGET, ""),
            Self::Claude => (claude::CHAT_TARGET, ""),
        }
    }

    /// Validate before file I/O or renewal; tokens never authorize a new destination.
    pub fn validate_target(self, target: &str) -> Result<(), AuthError> {
        match self {
            Self::Grok => grok::validate_target(target),
            Self::Codex => codex::validate_target(target),
            Self::Copilot => copilot::validate_target(target),
            Self::Qwen => qwen::validate_target(target),
            Self::Kimi => kimi::validate_target(target),
            Self::Gemini => gemini::validate_target(target),
            Self::Claude => claude::validate_target(target),
        }
    }
}

/// Fixed, content-free errors: no provider response, secret, file path, or chained HTTP error.
#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    #[error("invalid subscription configuration")]
    Configuration,
    #[error("subscription destination is not allowed")]
    Target,
    #[error("cannot read subscription credentials; sign in with the provider CLI")]
    Credentials,
    #[error("cannot refresh subscription credentials; sign in again")]
    Refresh,
    #[error("cannot update subscription credentials")]
    Store,
}

/// This value intentionally does not implement Debug or Serialize.
pub struct SubscriptionAuth {
    path: PathBuf,
    client: reqwest::Client,
    provider: Provider,
}

enum Provider {
    Grok(grok::Grok),
    Codex,
    Copilot(copilot::Copilot),
    Qwen(qwen::Qwen),
    Kimi(kimi::Kimi),
    Gemini(gemini::Gemini),
    Claude,
}

impl SubscriptionAuth {
    pub fn new(kind: Subscription, credentials: PathBuf) -> Result<Self, AuthError> {
        if credentials.as_os_str().is_empty() {
            return Err(AuthError::Configuration);
        }
        let provider = match kind {
            Subscription::Grok => Provider::Grok(grok::Grok::new()),
            Subscription::Codex => Provider::Codex,
            Subscription::Copilot => Provider::Copilot(copilot::Copilot::new()?),
            Subscription::Qwen => Provider::Qwen(qwen::Qwen::new()?),
            Subscription::Kimi => Provider::Kimi(kimi::Kimi::new()?),
            Subscription::Gemini => Provider::Gemini(gemini::Gemini::new()),
            Subscription::Claude => Provider::Claude,
        };
        Ok(Self {
            path: credentials,
            client: http::client()?,
            provider,
        })
    }

    pub fn kind(&self) -> Subscription {
        match self.provider {
            Provider::Grok(_) => Subscription::Grok,
            Provider::Codex => Subscription::Codex,
            Provider::Copilot(_) => Subscription::Copilot,
            Provider::Qwen(_) => Subscription::Qwen,
            Provider::Kimi(_) => Subscription::Kimi,
            Provider::Gemini(_) => Subscription::Gemini,
            Provider::Claude => Subscription::Claude,
        }
    }

    /// Dropping this future cancels token I/O and releases its OS lock; no detached
    /// refresh or persistence task outlives the caller.
    pub async fn headers(
        &self,
        target: &str,
        now: i64,
    ) -> Result<Vec<(String, String)>, AuthError> {
        self.kind().validate_target(target)?;
        match &self.provider {
            Provider::Grok(provider) => provider.headers(&self.path, &self.client, now).await,
            Provider::Codex => codex::headers(&self.path),
            Provider::Copilot(provider) => provider.headers(&self.path, &self.client, now).await,
            Provider::Qwen(provider) => provider.headers(&self.path, &self.client, now).await,
            Provider::Kimi(provider) => provider.headers(&self.path, &self.client, now).await,
            Provider::Gemini(provider) => provider
                .context(&self.path, &self.client, now)
                .await
                .map(|value| value.0),
            Provider::Claude => Ok(Vec::new()),
        }
    }

    /// Resolve an onboarded Gemini project and its read-only credential together.
    pub async fn gemini_context(
        &self,
        target: &str,
        now: i64,
    ) -> Result<(Vec<(String, String)>, String), AuthError> {
        let Provider::Gemini(provider) = &self.provider else {
            return Err(AuthError::Configuration);
        };
        gemini::validate_target(target)?;
        provider.context(&self.path, &self.client, now).await
    }

    /// Acquire a fresh grant without importing or rotating another CLI's refresh token.
    pub async fn login_device(&self, present: impl FnOnce(&str, &str)) -> Result<(), AuthError> {
        match &self.provider {
            Provider::Qwen(provider) => provider.login(&self.path, &self.client, present).await,
            Provider::Kimi(provider) => provider.login(&self.path, &self.client, present).await,
            Provider::Copilot(provider) => provider.login(&self.path, &self.client, present).await,
            _ => Err(AuthError::Configuration),
        }
    }
}

#[cfg(test)]
mod tests;
