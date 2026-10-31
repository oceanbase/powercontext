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

use crate::{AdmittedSend, SendOutcome, Transport};
use pc_gateway_evidence::EvidenceSink;
use std::num::NonZeroU32;

/// 有界重试：唯一所有者、有效准入、有界次数。
/// 只重试确定性 `NotSent`；`Indeterminate` 一律上抛，绝不自动付费重发。
pub struct BoundedRetry<T> {
    transport: T,
    max_attempts: NonZeroU32,
}

/// 重试的结果：`Done` 携带原始 `SendOutcome`；
/// `AllNotSent` 表示全部尝试都确定性未送达。
#[derive(Debug)]
pub enum RetryOutcome {
    Done(SendOutcome),
    AllNotSent { attempts: u32 },
}

impl<T: Transport> BoundedRetry<T> {
    pub fn new(transport: T, max_attempts: NonZeroU32) -> Self {
        Self {
            transport,
            max_attempts,
        }
    }

    pub fn transport(&self) -> &T {
        &self.transport
    }

    pub async fn send_with_retry(
        &self,
        send: &AdmittedSend,
        evidence: &dyn EvidenceSink,
    ) -> RetryOutcome {
        let mut attempt: u32 = 0;
        loop {
            attempt += 1;
            evidence.record_attempt(attempt);
            match self.transport.send(send.clone()).await {
                // 可能已送达：立即上抛，不做任何自动重发。
                outcome @ SendOutcome::Indeterminate(_) | outcome @ SendOutcome::Delivered(_) => {
                    return RetryOutcome::Done(outcome)
                }
                SendOutcome::NotSent(reason) => {
                    if attempt >= self.max_attempts.get() {
                        evidence.record_not_sent(attempt, &reason.to_string());
                        return RetryOutcome::AllNotSent { attempts: attempt };
                    }
                    // 确定性未送达：唯一所有者继续重试。
                }
            }
        }
    }
}
