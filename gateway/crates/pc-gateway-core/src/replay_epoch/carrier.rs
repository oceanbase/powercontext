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

use std::fmt;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};

use super::ReplayError;

/// Opaque server-issued lookup value; use `to_header_value` only for explicit storage or transmission.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct ReplayHandle(pub(super) [u8; 32]);

impl ReplayHandle {
    pub fn parse(value: &str) -> Result<Self, ReplayError> {
        let (version, encoded) = value.split_once('.').ok_or(ReplayError::InvalidCarrier)?;
        let digits = version
            .strip_prefix('v')
            .ok_or(ReplayError::InvalidCarrier)?;
        if digits.is_empty()
            || digits.starts_with('0')
            || !digits.bytes().all(|byte| byte.is_ascii_digit())
        {
            return Err(ReplayError::InvalidCarrier);
        }
        let bytes = decode(encoded)?;
        if digits != "1" {
            return Err(ReplayError::UnsupportedCarrier);
        }
        Ok(Self(bytes))
    }

    pub fn to_header_value(&self) -> String {
        format!("v1.{}", URL_SAFE_NO_PAD.encode(self.0))
    }
}

impl fmt::Debug for ReplayHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ReplayHandle([REDACTED])")
    }
}

impl fmt::Display for ReplayHandle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("[REDACTED]")
    }
}

/// Host-generated attempt reference; possession grants no permission or model idempotency.
///
/// The wire format is exactly 43 unpadded base64url characters: an 8-byte big-endian Unix
/// millisecond deadline followed by 24 cryptographically random bytes. Hosts must generate this
/// layout rather than an arbitrary random payload, and save and reuse the entire value unchanged.
/// The deadline limits establishment, not recovery of an existing association.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct EstablishmentReference([u8; 32]);

impl EstablishmentReference {
    pub(super) const MAX_WINDOW: Duration = Duration::from_secs(60);

    pub fn parse(value: &str) -> Result<Self, ReplayError> {
        decode(value).map(Self)
    }

    /// Generate before recording the attempt and starting network I/O. The deadline is set to
    /// 30 seconds after the host's current time, rounded down to the nearest millisecond.
    pub fn generate() -> Result<Self, ReplayError> {
        let millis = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .ok()
            .and_then(|duration| duration.checked_add(Duration::from_secs(30)))
            .and_then(|duration| u64::try_from(duration.as_millis()).ok())
            .ok_or(ReplayError::DependencyUnavailable)?;
        let mut bytes = [0; 32];
        bytes[..8].copy_from_slice(&millis.to_be_bytes());
        getrandom::getrandom(&mut bytes[8..]).map_err(|_| ReplayError::DependencyUnavailable)?;
        Ok(Self(bytes))
    }

    pub fn to_header_value(&self) -> String {
        URL_SAFE_NO_PAD.encode(self.0)
    }

    pub(super) fn establishment_deadline(&self) -> Option<SystemTime> {
        let millis = u64::from_be_bytes(*self.0.first_chunk::<8>()?);
        UNIX_EPOCH.checked_add(Duration::from_millis(millis))
    }
}

impl fmt::Debug for EstablishmentReference {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("EstablishmentReference([REDACTED])")
    }
}

impl fmt::Display for EstablishmentReference {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("[REDACTED]")
    }
}

/// Parsed model-request controls. Absence requires store resolution before selecting check-only.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReplayCarrier {
    Absent,
    Establish(EstablishmentReference),
    Continue(ReplayHandle),
}

impl ReplayCarrier {
    /// Pass every occurrence of both control headers, without trimming or joining values.
    /// `conversation` is already normalized by its owning host identity boundary.
    pub fn parse(
        replay: &[&str],
        establishment: &[&str],
        conversation: Option<&str>,
    ) -> Result<Self, ReplayError> {
        match (replay, establishment) {
            ([], []) => Ok(Self::Absent),
            (["new"], [reference]) if conversation.is_some_and(|value| !value.is_empty()) => {
                Ok(Self::Establish(EstablishmentReference::parse(reference)?))
            }
            ([handle], []) if conversation.is_some_and(|value| !value.is_empty()) => {
                Ok(Self::Continue(ReplayHandle::parse(handle)?))
            }
            _ => Err(ReplayError::InvalidCarrier),
        }
    }
}

fn decode(value: &str) -> Result<[u8; 32], ReplayError> {
    if value.len() != 43 {
        return Err(ReplayError::InvalidCarrier);
    }
    let decoded = URL_SAFE_NO_PAD
        .decode(value)
        .map_err(|_| ReplayError::InvalidCarrier)?;
    decoded.try_into().map_err(|_| ReplayError::InvalidCarrier)
}
