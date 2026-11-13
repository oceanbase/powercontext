// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::AuthError;

pub(super) const CHAT_TARGET: &str = "claude-cli://execute";

pub(super) fn validate_target(target: &str) -> Result<(), AuthError> {
    (target == CHAT_TARGET)
        .then_some(())
        .ok_or(AuthError::Target)
}
