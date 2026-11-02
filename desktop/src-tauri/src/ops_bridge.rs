/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

//! Native-only approvals and durable references for independent Ops v1 invocations.

use serde_json::{Value, json};
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
use uuid::Uuid;

mod process;
mod trust;
mod types;
mod wire;
pub use types::*;

const APPROVAL_TTL: Duration = Duration::from_secs(600);
const MAX_APPROVALS: usize = 32;
pub const PROGRESS_EVENT: &str = "ops-progress";

#[derive(Clone)]
pub struct OpsHost(Arc<Host>);
struct Host {
    trust: trust::Trust,
    approvals: Mutex<HashMap<String, Approval>>,
    references: Mutex<trust::References>,
    active: Arc<Active>,
    #[cfg(test)]
    deadline: Option<Duration>,
}
struct Approval {
    plan: Value,
    binding: String,
    resume: Option<OpsResumeReference>,
    created: Instant,
}
#[derive(Default)]
struct Active {
    counts: Mutex<[usize; 2]>,
    ready: Condvar,
    closing: AtomicBool,
}
struct CallGuard {
    active: Arc<Active>,
    lane: usize,
}
impl Drop for CallGuard {
    fn drop(&mut self) {
        if let Ok(mut counts) = self.active.counts.lock() {
            counts[self.lane] -= 1;
            self.active.ready.notify_all();
        }
    }
}

impl OpsHost {
    pub fn new(
        resources: Option<PathBuf>,
        local_data: Option<PathBuf>,
        home: Option<PathBuf>,
    ) -> Self {
        let trust = trust::Trust::new(resources, local_data, home);
        Self(Arc::new(Host {
            references: Mutex::new(trust::References::new(trust.support.clone())),
            trust,
            approvals: Mutex::new(HashMap::new()),
            active: Arc::new(Active::default()),
            #[cfg(test)]
            deadline: None,
        }))
    }
    pub fn shutdown(&self) {
        self.0.active.closing.store(true, Ordering::Release);
        if let Ok(counts) = self.0.active.counts.lock() {
            let _ = self
                .0
                .active
                .ready
                .wait_timeout_while(counts, Duration::from_secs(2), |c| {
                    c.iter().sum::<usize>() > 0
                });
        }
    }
    fn enter(&self, action: OpsAction) -> Result<CallGuard, OpsBridgeError> {
        let lane = usize::from(matches!(
            action,
            OpsAction::OperationStatus | OpsAction::Cancel
        ));
        let mut counts = self
            .0
            .active
            .counts
            .lock()
            .map_err(|_| OpsBridgeError::Storage)?;
        if self.0.active.closing.load(Ordering::Acquire) {
            return Err(OpsBridgeError::ShuttingDown);
        }
        // Two reserved control slots stay available while ordinary requests are running.
        if counts[lane] >= [4, 2][lane] {
            return Err(OpsBridgeError::Busy);
        }
        counts[lane] += 1;
        Ok(CallGuard {
            active: self.0.active.clone(),
            lane,
        })
    }
    pub fn references(&self) -> Result<OpsReferences, OpsBridgeError> {
        let binding = self.0.trust.binding().ok();
        let items = self
            .0
            .references
            .lock()
            .map_err(|_| OpsBridgeError::Storage)?
            .read()?;
        Ok(OpsReferences {
            binding_available: binding.is_some(),
            operations: items
                .into_iter()
                .map(|item| OpsOperationReference {
                    operation_id: item.operation_id,
                    binding_status: match &binding {
                        Some(b) if b.fingerprint == item.binding_id => {
                            OpsReferenceBindingStatus::Current
                        }
                        Some(_) => OpsReferenceBindingStatus::Different,
                        None => OpsReferenceBindingStatus::Unavailable,
                    },
                })
                .collect(),
        })
    }
    fn require_reference(&self, id: &str, binding: &str) -> Result<(), OpsBridgeError> {
        wire::identifier(id).map_err(|_| OpsBridgeError::InvalidInput)?;
        self.0
            .references
            .lock()
            .map_err(|_| OpsBridgeError::Storage)?
            .require(id, binding)
    }
    pub fn inspect(&self) -> OpsInspectionReply {
        let result = self.read(OpsAction::Inspect);
        match result.and_then(|(v, code)| wire::inspection(&v, code)) {
            Ok(reply) => reply,
            Err(error) => OpsInspectionReply {
                outcome: OpsOutcome::Unknown,
                components: None,
                error: None,
                bridge_error: Some(error),
                exit_code: None,
            },
        }
    }
    pub fn config_validate(&self) -> OpsValidationReply {
        match self
            .read(OpsAction::ConfigValidate)
            .and_then(|(v, code)| wire::validation(&v, code))
        {
            Ok(reply) => reply,
            Err(error) => OpsValidationReply {
                outcome: OpsOutcome::Unknown,
                configuration: OpsConfigurationState::Unknown,
                error: None,
                bridge_error: Some(error),
                exit_code: None,
            },
        }
    }
    fn read(&self, action: OpsAction) -> Result<(Value, i32), OpsBridgeError> {
        let guard = self.enter(action)?;
        let binding = self.0.trust.binding()?;
        self.invoke(&binding, action, request(action, json!({})), guard, |_| {})
    }
    pub fn preflight(&self, input: OpsPreflightInput) -> Result<OpsPreflightReply, OpsBridgeError> {
        input.options.validate(input.operation)?;
        let guard = self.enter(OpsAction::Preflight)?;
        let binding = self.0.trust.binding()?;
        if let Some(resume) = &input.resume {
            if !(1..64).contains(&resume.expected_attempt) {
                return Err(OpsBridgeError::InvalidInput);
            }
            self.require_reference(&resume.operation_id, &binding.fingerprint)?;
        }
        let body = request(
            OpsAction::Preflight,
            json!({"operation":input.operation.wire_name(), "options":input.options.wire_value()}),
        );
        let response = self.invoke(&binding, OpsAction::Preflight, body, guard, |_| {});
        let (message, code) = match response {
            Ok(value) => value,
            Err(error) => {
                return Ok(OpsPreflightReply {
                    outcome: OpsOutcome::Unknown,
                    approval: None,
                    error: None,
                    bridge_error: Some(error),
                    exit_code: None,
                });
            }
        };
        if message["outcome"] == "rejected" {
            return Ok(OpsPreflightReply {
                outcome: OpsOutcome::Rejected,
                approval: None,
                error: Some(wire::error(&message["error"])?),
                bridge_error: None,
                exit_code: Some(code),
            });
        }
        let plan = &message["plan"];
        let mut summary = wire::plan(plan, input.operation, &input.options)?;
        if binding
            .target_digest
            .as_ref()
            .is_some_and(|digest| plan["binding_id"] != *digest)
        {
            return Err(OpsBridgeError::InvalidResponse);
        }
        if binding
            .target_id
            .as_ref()
            .is_some_and(|id| id != &summary.target.target_id)
        {
            return Err(OpsBridgeError::InvalidResponse);
        }
        if let Some(digest) = binding.catalog_digest.as_ref()
            && plan
                .get("installation")
                .is_some_and(|v| v["catalog_sha256"] != *digest)
        {
            return Err(OpsBridgeError::InvalidResponse);
        }
        let mut approvals = self
            .0
            .approvals
            .lock()
            .map_err(|_| OpsBridgeError::Storage)?;
        approvals.retain(|_, a| a.created.elapsed() < APPROVAL_TTL);
        if approvals.len() >= MAX_APPROVALS {
            return Err(OpsBridgeError::Busy);
        }
        let id = Uuid::new_v4().to_string();
        summary.approval_id = id.clone();
        approvals.insert(
            id,
            Approval {
                plan: plan.clone(),
                binding: binding.fingerprint,
                resume: input.resume,
                created: Instant::now(),
            },
        );
        Ok(OpsPreflightReply {
            outcome: OpsOutcome::Succeeded,
            approval: Some(summary),
            error: None,
            bridge_error: None,
            exit_code: Some(code),
        })
    }
    pub fn apply(
        &self,
        input: OpsApprovalInput,
        emit: impl Fn(OpsProgress) + Send + 'static,
    ) -> Result<OpsOperationReply, OpsBridgeError> {
        self.approved(input, false, emit)
    }
    pub fn resume(
        &self,
        input: OpsApprovalInput,
        emit: impl Fn(OpsProgress) + Send + 'static,
    ) -> Result<OpsOperationReply, OpsBridgeError> {
        self.approved(input, true, emit)
    }
    fn approved(
        &self,
        input: OpsApprovalInput,
        resume: bool,
        emit: impl Fn(OpsProgress) + Send + 'static,
    ) -> Result<OpsOperationReply, OpsBridgeError> {
        if Uuid::parse_str(&input.approval_id).is_err() || !trust::is_digest(&input.plan_id) {
            return Err(OpsBridgeError::InvalidInput);
        }
        let action = if resume {
            OpsAction::Resume
        } else {
            OpsAction::Apply
        };
        let guard = self.enter(action)?;
        let binding = self.0.trust.binding()?;
        let approval = self
            .0
            .approvals
            .lock()
            .map_err(|_| OpsBridgeError::Storage)?
            .remove(&input.approval_id)
            .ok_or(OpsBridgeError::StaleApproval)?;
        if approval.created.elapsed() >= APPROVAL_TTL
            || approval.binding != binding.fingerprint
            || approval.plan["plan_id"] != input.plan_id
            || approval.resume.is_some() != resume
        {
            return Err(OpsBridgeError::StaleApproval);
        }
        let id = approval
            .resume
            .as_ref()
            .map(|r| r.operation_id.clone())
            .unwrap_or_else(|| Uuid::new_v4().to_string());
        if resume {
            self.require_reference(&id, &binding.fingerprint)?;
        }
        // The reference is durable before any possibility of dispatch. It is not an outcome.
        self.0
            .references
            .lock()
            .map_err(|_| OpsBridgeError::Storage)?
            .add(&id, &binding.fingerprint)?;
        let mut fields = json!({"operation_id":id, "plan":approval.plan, "authorization":{"plan_id":input.plan_id,"approved":true}});
        if let Some(reference) = approval.resume {
            fields["expected_attempt"] = reference.expected_attempt.into();
        }
        self.operation(&binding, action, id, fields, guard, emit)
    }
    pub fn operation_status(
        &self,
        input: OpsStatusInput,
    ) -> Result<OpsOperationReply, OpsBridgeError> {
        let guard = self.enter(OpsAction::OperationStatus)?;
        let binding = self.0.trust.binding()?;
        self.require_reference(&input.operation_id, &binding.fingerprint)?;
        self.operation(
            &binding,
            OpsAction::OperationStatus,
            input.operation_id.clone(),
            json!({"operation_id":input.operation_id}),
            guard,
            |_| {},
        )
    }
    pub fn cancel(&self, input: OpsCancelInput) -> Result<OpsOperationReply, OpsBridgeError> {
        if !(1..=64).contains(&input.attempt) {
            return Err(OpsBridgeError::InvalidInput);
        }
        let guard = self.enter(OpsAction::Cancel)?;
        let binding = self.0.trust.binding()?;
        self.require_reference(&input.operation_id, &binding.fingerprint)?;
        self.operation(
            &binding,
            OpsAction::Cancel,
            input.operation_id.clone(),
            json!({"operation_id":input.operation_id,"attempt":input.attempt}),
            guard,
            |_| {},
        )
    }
    fn operation(
        &self,
        binding: &trust::Binding,
        action: OpsAction,
        id: String,
        fields: Value,
        guard: CallGuard,
        emit: impl Fn(OpsProgress) + Send + 'static,
    ) -> Result<OpsOperationReply, OpsBridgeError> {
        match self
            .invoke(binding, action, request(action, fields), guard, emit)
            .and_then(|(v, c)| wire::operation(&v, &id, c))
        {
            Ok(reply) => Ok(reply),
            Err(error) => Ok(OpsOperationReply {
                operation_id: id,
                outcome: OpsOutcome::Unknown,
                attempt: None,
                exit_code: None,
                error: None,
                bridge_error: Some(error),
                status: None,
            }),
        }
    }
    fn invoke(
        &self,
        binding: &trust::Binding,
        action: OpsAction,
        body: Value,
        guard: CallGuard,
        emit: impl Fn(OpsProgress) + Send + 'static,
    ) -> Result<(Value, i32), OpsBridgeError> {
        let launch = self.0.trust.launch(binding)?;
        let deadline = if matches!(action, OpsAction::Apply | OpsAction::Resume) {
            Duration::from_secs(1800)
        } else if action == OpsAction::Preflight {
            Duration::from_secs(180)
        } else {
            Duration::from_secs(30)
        };
        #[cfg(test)]
        let deadline = self.0.deadline.unwrap_or(deadline);
        process::run(launch, body, deadline, guard, emit)
    }
}
fn request(action: OpsAction, mut fields: Value) -> Value {
    fields["protocol_version"] = 1.into();
    fields["request_id"] = Uuid::new_v4().to_string().into();
    fields["action"] = action.wire_name().into();
    fields
}
#[cfg(all(test, target_os = "macos"))]
mod tests;
