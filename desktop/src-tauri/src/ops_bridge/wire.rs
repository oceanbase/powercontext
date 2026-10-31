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

//! Strict Ops v1 parsing and allowlisted renderer projections.
use super::{
    trust::{is_digest, sha256},
    *,
};
use serde::{
    Deserialize,
    de::{self, DeserializeOwned, MapAccess, SeqAccess, Visitor},
};
use serde_json::{Map, Value};
type Result<T> = std::result::Result<T, OpsBridgeError>;
const INVALID: OpsBridgeError = OpsBridgeError::InvalidResponse;

pub(super) fn identifier(s: &str) -> Result<()> {
    if s.is_empty()
        || s.len() > 96
        || !s.as_bytes()[0].is_ascii_alphanumeric()
        || !s
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        Err(INVALID)
    } else {
        Ok(())
    }
}
fn object<'a>(
    v: &'a Value,
    required: &[&str],
    optional: &[&str],
) -> Result<&'a Map<String, Value>> {
    let o = v.as_object().ok_or(INVALID)?;
    if required.iter().any(|k| !o.contains_key(*k))
        || o.keys()
            .any(|k| !required.contains(&k.as_str()) && !optional.contains(&k.as_str()))
    {
        return Err(INVALID);
    }
    Ok(o)
}
fn parse<T: DeserializeOwned>(v: &Value) -> Result<T> {
    serde_json::from_value(v.clone()).map_err(|_| INVALID)
}
fn optional<T>(v: &Value, f: impl FnOnce(&Value) -> Result<T>) -> Result<Option<T>> {
    if v.is_null() {
        Ok(None)
    } else {
        f(v).map(Some)
    }
}
fn text(v: &Value, max: usize) -> Result<String> {
    let s = v.as_str().ok_or(INVALID)?;
    if s.is_empty() || s.len() > max || s.chars().any(char::is_control) {
        Err(INVALID)
    } else {
        Ok(s.into())
    }
}
fn id(v: &Value) -> Result<String> {
    let s = text(v, 96)?;
    identifier(&s)?;
    Ok(s)
}
fn hash(v: &Value) -> Result<String> {
    let s = text(v, 64)?;
    if !is_digest(&s) {
        return Err(INVALID);
    }
    Ok(s)
}
fn boolean(v: &Value) -> Result<bool> {
    v.as_bool().ok_or(INVALID)
}
fn attempt(v: &Value) -> Result<u32> {
    let n = v.as_u64().filter(|v| (1..=64).contains(v)).ok_or(INVALID)?;
    Ok(n as u32)
}
fn endpoint(v: &Value) -> Result<String> {
    let s = text(v, 2048)?;
    let u = url::Url::parse(&s).map_err(|_| INVALID)?;
    if u.scheme() != "http"
        || !u.username().is_empty()
        || u.password().is_some()
        || u.query().is_some()
        || u.fragment().is_some()
        || !matches!(u.host_str(), Some("127.0.0.1" | "localhost" | "[::1]"))
    {
        return Err(INVALID);
    }
    Ok(s)
}
pub(super) fn error(v: &Value) -> Result<OpsErrorCode> {
    object(v, &["code"], &[])?;
    parse(&v["code"])
}
fn recovery(v: &Value) -> Result<Vec<OpsRecoveryAction>> {
    if v.is_null() {
        return Ok(vec![]);
    }
    let list = v.as_array().filter(|a| a.len() <= 3).ok_or(INVALID)?;
    list.iter().map(parse).collect()
}
fn database(v: &Value) -> Result<OpsDatabaseClassification> {
    object(
        v,
        &[
            "classification",
            "allowed_to_start",
            "fingerprint",
            "reason",
        ],
        &[],
    )?;
    boolean(&v["allowed_to_start"])?;
    hash(&v["fingerprint"])?;
    identifier(v["reason"].as_str().ok_or(INVALID)?)?;
    parse(&v["classification"])
}
fn components(v: &Value) -> Result<OpsComponents> {
    object(
        v,
        &[
            "support",
            "registration",
            "definition",
            "manager_ownership",
            "manager",
            "liveness",
            "readiness",
            "endpoint",
            "maintenance",
            "resources",
            "configuration",
        ],
        &[
            "installation_id",
            "resource_id",
            "resource_digest",
            "database",
            "connection_compatibility",
            "installation_stage",
            "installation_observation",
        ],
    )?;
    Ok(OpsComponents {
        support: Some(parse(&v["support"])?),
        registration: Some(parse(&v["registration"])?),
        definition: Some(parse(&v["definition"])?),
        manager_ownership: Some(parse(&v["manager_ownership"])?),
        manager: Some(parse(&v["manager"])?),
        liveness: Some(parse(&v["liveness"])?),
        readiness: Some(parse(&v["readiness"])?),
        endpoint: optional(&v["endpoint"], endpoint)?,
        maintenance: optional(&v["maintenance"], parse)?,
        resources: Some(parse(&v["resources"])?),
        configuration: Some(parse(&v["configuration"])?),
        installation_id: optional(&v["installation_id"], id)?,
        resource_id: optional(&v["resource_id"], id)?,
        resource_digest: optional(&v["resource_digest"], hash)?,
        database: optional(&v["database"], database)?,
        connection_compatibility: optional(&v["connection_compatibility"], parse)?,
        installation_stage: optional(&v["installation_stage"], parse)?,
        installation_observation: optional(&v["installation_observation"], parse)?,
    })
}
const ENVELOPE: &[&str] = &["protocol_version", "request_id", "type", "outcome"];
fn rejected(v: &Value) -> Result<()> {
    object(v, ENVELOPE, &["error", "recovery_actions"])?;
    if v["outcome"] != "rejected" {
        return Err(INVALID);
    }
    error(&v["error"])?;
    recovery(&v["recovery_actions"])?;
    Ok(())
}
pub(super) fn inspection(v: &Value, code: i32) -> Result<OpsInspectionReply> {
    let (outcome, components, error) = if v["outcome"] == "rejected" {
        rejected(v)?;
        (OpsOutcome::Rejected, None, Some(error(&v["error"])?))
    } else {
        object(v, ENVELOPE, &["components"])?;
        if v["outcome"] != "succeeded" {
            return Err(INVALID);
        }
        (
            OpsOutcome::Succeeded,
            Some(components(&v["components"])?),
            None,
        )
    };
    Ok(OpsInspectionReply {
        outcome,
        components,
        error,
        bridge_error: None,
        exit_code: Some(code),
    })
}
pub(super) fn validation(v: &Value, code: i32) -> Result<OpsValidationReply> {
    let (outcome, configuration, error) = if v["outcome"] == "rejected" {
        rejected(v)?;
        (
            OpsOutcome::Rejected,
            OpsConfigurationState::Unknown,
            Some(error(&v["error"])?),
        )
    } else {
        object(v, ENVELOPE, &["components"])?;
        object(&v["components"], &["configuration"], &[])?;
        if v["outcome"] != "succeeded" || v["components"]["configuration"] != "valid" {
            return Err(INVALID);
        }
        (OpsOutcome::Succeeded, OpsConfigurationState::Valid, None)
    };
    Ok(OpsValidationReply {
        outcome,
        configuration,
        error,
        bridge_error: None,
        exit_code: Some(code),
    })
}
pub(super) fn plan(
    v: &Value,
    operation: OpsOperation,
    options: &OpsOptions,
) -> Result<OpsPlanApproval> {
    let install = matches!(operation, OpsOperation::Install | OpsOperation::Repair);
    object(
        v,
        &[
            "protocol_version",
            "target_id",
            "binding_id",
            "operation",
            "options",
            "before",
            "effects",
            "target_summary",
            "plan_id",
        ],
        if install { &["installation"] } else { &[] },
    )?;
    if v["protocol_version"] != 1
        || v["operation"] != operation.wire_name()
        || v["options"] != options.wire_value()
    {
        return Err(INVALID);
    }
    let plan_id = hash(&v["plan_id"])?;
    hash(&v["binding_id"])?;
    hash(&v["before"])?;
    let mut unsigned = v.clone();
    unsigned.as_object_mut().ok_or(INVALID)?.remove("plan_id");
    if sha256(&serde_json::to_vec(&unsigned).map_err(|_| INVALID)?) != plan_id {
        return Err(INVALID);
    }
    object(&v["effects"], &["may_start", "preserves_data"], &[])?;
    if !boolean(&v["effects"]["preserves_data"])? {
        return Err(INVALID);
    }
    let t = &v["target_summary"];
    object(
        t,
        &[
            "python_executable",
            "package_version",
            "env_file",
            "endpoint",
            "data_dir",
            "start_on_login",
        ],
        &[],
    )?;
    let target = OpsTargetSummary {
        target_id: id(&v["target_id"])?,
        python_executable: text(&t["python_executable"], 4096)?,
        package_version: text(&t["package_version"], 128)?,
        env_file: text(&t["env_file"], 4096)?,
        endpoint: optional(&t["endpoint"], endpoint)?,
        data_dir: optional(&t["data_dir"], |v| text(v, 4096))?,
        start_on_login: boolean(&t["start_on_login"])?,
    };
    let installation = if install {
        let i = &v["installation"];
        object(
            i,
            &[
                "catalog_sha256",
                "resource",
                "layout",
                "installation_id",
                "classification",
                "resources",
                "resource_identity",
                "configuration",
                "database",
                "service_before",
                "space_required",
                "verify_running",
                "preserve_stopped",
                "repair_owner",
                "registration_present",
            ],
            &["registration_removal"],
        )?;
        hash(&i["catalog_sha256"])?;
        hash(&i["service_before"])?;
        for field in ["installation_id", "repair_owner", "registration_removal"] {
            optional(&i[field], id)?;
        }
        for field in ["resource", "layout", "configuration"] {
            if !i[field].is_object() {
                return Err(INVALID);
            }
        }
        if !i["resource_identity"].is_null() && !i["resource_identity"].is_object() {
            return Err(INVALID);
        }
        Some(OpsInstallationSummary {
            classification: parse(&i["classification"])?,
            resources: parse(&i["resources"])?,
            database: database(&i["database"])?,
            space_required: i["space_required"]
                .as_u64()
                .filter(|n| *n <= 64 * 1024 * 1024 * 1024)
                .ok_or(INVALID)?,
            verify_running: boolean(&i["verify_running"])?,
            preserve_stopped: boolean(&i["preserve_stopped"])?,
            registration_present: boolean(&i["registration_present"])?,
        })
    } else {
        None
    };
    Ok(OpsPlanApproval {
        approval_id: String::new(),
        plan_id,
        operation,
        options: options.clone(),
        target,
        effects: OpsPlanEffects {
            may_start: boolean(&v["effects"]["may_start"])?,
            preserves_data: true,
        },
        installation,
    })
}
fn stored(v: &Value) -> Result<OpsStoredResult> {
    object(
        v,
        &["outcome"],
        &["components", "error", "recovery_actions"],
    )?;
    let outcome = parse(&v["outcome"])?;
    if !matches!(
        outcome,
        OpsOutcome::Succeeded
            | OpsOutcome::Cancelled
            | OpsOutcome::Failed
            | OpsOutcome::PartialFailure
            | OpsOutcome::Uncertain
    ) {
        return Err(INVALID);
    }
    Ok(OpsStoredResult {
        outcome,
        components: optional(&v["components"], components)?,
        error: optional(&v["error"], error)?,
        recovery_actions: recovery(&v["recovery_actions"])?,
    })
}
pub(super) fn operation(v: &Value, id: &str, code: i32) -> Result<OpsOperationReply> {
    if v["outcome"] == "rejected" {
        rejected(v)?;
        return Ok(OpsOperationReply {
            operation_id: id.into(),
            outcome: OpsOutcome::Rejected,
            attempt: None,
            exit_code: Some(code),
            error: Some(error(&v["error"])?),
            bridge_error: None,
            status: None,
        });
    }
    if v["operation_id"] != id {
        return Err(INVALID);
    }
    let number = attempt(&v["attempt"])?;
    let outcome: OpsOutcome = parse(&v["outcome"])?;
    let status = if v.get("phase").is_some() {
        let status_fields = [
            ENVELOPE,
            &[
                "operation_id",
                "attempt",
                "phase",
                "executor",
                "execution_state",
                "cancel_requested",
                "result",
                "observation",
                "history",
                "recovery_actions",
            ],
        ]
        .concat();
        object(v, &status_fields, &[])?;
        object(&v["executor"], &["pid", "observation"], &[])?;
        let executor = OpsExecutor {
            pid: optional(&v["executor"]["pid"], |v| {
                v.as_u64()
                    .filter(|n| *n > 0 && *n <= u32::MAX as u64)
                    .ok_or(INVALID)
            })?,
            observation: parse(&v["executor"]["observation"])?,
        };
        let result = optional(&v["result"], stored)?;
        if result
            .as_ref()
            .map_or(outcome != OpsOutcome::Pending, |r| r.outcome != outcome)
        {
            return Err(INVALID);
        }
        let execution_state: OpsExecutionState = parse(&v["execution_state"])?;
        let expected_execution = if result.is_some() {
            OpsExecutionState::Finished
        } else if executor.observation == OpsExecutorObservation::Exited {
            OpsExecutionState::Interrupted
        } else {
            OpsExecutionState::Unknown
        };
        if execution_state != expected_execution {
            return Err(INVALID);
        }
        let observation = if v["observation"].get("state").is_some() {
            object(&v["observation"], &["state", "error"], &[])?;
            OpsObservation {
                state: Some(parse(&v["observation"]["state"])?),
                components: None,
                error: Some(error(&v["observation"]["error"])?),
            }
        } else {
            OpsObservation {
                state: None,
                components: Some(components(&v["observation"])?),
                error: None,
            }
        };
        let history = v["history"]
            .as_array()
            .filter(|a| !a.is_empty() && a.len() <= 64 && number as usize <= a.len())
            .ok_or(INVALID)?
            .iter()
            .enumerate()
            .map(|(index, item)| {
                object(item, &["attempt", "outcome"], &[])?;
                if attempt(&item["attempt"])? as usize != index + 1 {
                    return Err(INVALID);
                }
                let historical = optional(&item["outcome"], parse)?;
                if historical.is_some_and(|o| {
                    !matches!(
                        o,
                        OpsOutcome::Succeeded
                            | OpsOutcome::Cancelled
                            | OpsOutcome::Failed
                            | OpsOutcome::PartialFailure
                            | OpsOutcome::Uncertain
                    )
                }) {
                    return Err(INVALID);
                }
                Ok(OpsAttemptSummary {
                    attempt: (index + 1) as u32,
                    outcome: historical,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        if history[number as usize - 1].outcome != result.as_ref().map(|r| r.outcome) {
            return Err(INVALID);
        }
        Some(OpsOperationStatus {
            operation_id: id.into(),
            attempt: number,
            phase: parse(&v["phase"])?,
            executor,
            execution_state,
            outcome,
            cancel_requested: optional(&v["cancel_requested"], boolean)?,
            result,
            observation,
            history,
            recovery_actions: recovery(&v["recovery_actions"])?,
        })
    } else {
        object(
            v,
            ENVELOPE,
            &["operation_id", "attempt", "error", "recovery_actions"],
        )?;
        if !matches!(outcome, OpsOutcome::Failed | OpsOutcome::Uncertain)
            || error(&v["error"])? != OpsErrorCode::CheckpointFailed
        {
            return Err(INVALID);
        }
        recovery(&v["recovery_actions"])?;
        None
    };
    Ok(OpsOperationReply {
        operation_id: id.into(),
        outcome,
        attempt: Some(number),
        exit_code: Some(code),
        error: optional(&v["error"], error)?,
        bridge_error: None,
        status,
    })
}

pub(super) struct Stream {
    request: Value,
    expected_attempt: Option<u32>,
    accepted: Option<u32>,
    sequence: u32,
    result: Option<Value>,
    messages: usize,
}
impl Stream {
    pub fn new(request: &Value) -> Result<Self> {
        let expected_attempt = match request["action"].as_str() {
            // Native apply always allocates a fresh operation ID; it never replays apply.
            Some("apply") => Some(1),
            Some("resume") => Some(
                attempt(&request["expected_attempt"])?
                    .checked_add(1)
                    .filter(|n| *n <= 64)
                    .ok_or(INVALID)?,
            ),
            Some("cancel") => Some(attempt(&request["attempt"])?),
            _ => None,
        };
        Ok(Self {
            request: request.clone(),
            expected_attempt,
            accepted: None,
            sequence: 0,
            result: None,
            messages: 0,
        })
    }
    pub fn line(&mut self, bytes: &[u8]) -> Result<Option<OpsProgress>> {
        self.messages += 1;
        if self.messages > 128 || self.result.is_some() || bytes.is_empty() {
            return Err(INVALID);
        }
        let v: Unique = serde_json::from_slice(bytes).map_err(|_| INVALID)?;
        let v = v.0;
        if v["protocol_version"].as_u64() != Some(1)
            || v["request_id"] != self.request["request_id"]
        {
            return Err(INVALID);
        }
        match v["type"].as_str() {
            Some("accepted" | "progress") => {
                if !matches!(self.request["action"].as_str(), Some("apply" | "resume"))
                    || v["operation_id"] != self.request["operation_id"]
                {
                    return Err(INVALID);
                }
                let accepted = v["type"] == "accepted";
                object(
                    &v,
                    &[
                        "protocol_version",
                        "request_id",
                        "type",
                        "operation_id",
                        "attempt",
                    ],
                    if accepted {
                        &[]
                    } else {
                        &["sequence", "phase"]
                    },
                )?;
                let number = attempt(&v["attempt"])?;
                if Some(number) != self.expected_attempt {
                    return Err(INVALID);
                }
                let (sequence, phase) = if accepted {
                    if self.accepted.replace(number).is_some() {
                        return Err(INVALID);
                    }
                    (None, None)
                } else {
                    if self.accepted != Some(number) {
                        return Err(INVALID);
                    }
                    let sequence = v["sequence"]
                        .as_u64()
                        .filter(|n| *n > u64::from(self.sequence) && *n <= 8)
                        .ok_or(INVALID)? as u32;
                    let phase: OpsServicePhase = parse(&v["phase"])?;
                    let expected = match phase {
                        OpsServicePhase::Service | OpsServicePhase::Preparing => 1,
                        OpsServicePhase::Prepared => 2,
                        OpsServicePhase::Publishing => 3,
                        OpsServicePhase::ResourcesPublished => 4,
                        OpsServicePhase::ConfigurationPrepared => 5,
                        OpsServicePhase::Configured => 6,
                        OpsServicePhase::Registering => 7,
                        OpsServicePhase::Complete => 8,
                        _ => return Err(INVALID),
                    };
                    if sequence != expected {
                        return Err(INVALID);
                    }
                    self.sequence = sequence;
                    (Some(sequence), Some(phase))
                };
                Ok(Some(OpsProgress {
                    request_id: id(&v["request_id"])?,
                    operation_id: id(&v["operation_id"])?,
                    attempt: number,
                    sequence,
                    phase,
                    accepted,
                }))
            }
            Some("result") => {
                match self.request["action"].as_str() {
                    Some("inspect") => {
                        inspection(&v, 0)?;
                    }
                    Some("config_validate") => {
                        validation(&v, 0)?;
                    }
                    Some("preflight") => {
                        if v["outcome"] == "rejected" {
                            rejected(&v)?;
                        } else {
                            object(&v, ENVELOPE, &["plan"])?;
                            if v["outcome"] != "succeeded" {
                                return Err(INVALID);
                            }
                            let op = parse(&self.request["operation"])?;
                            let options = wire_options(&self.request["options"])?;
                            plan(&v["plan"], op, &options)?;
                        }
                    }
                    Some("apply" | "resume" | "operation_status" | "cancel") => {
                        let result = operation(
                            &v,
                            self.request["operation_id"].as_str().ok_or(INVALID)?,
                            0,
                        )?;
                        // Duplicate resume and checkpoint failures can omit accepted.
                        // Their final result still belongs to the requested attempt.
                        if result
                            .attempt
                            .zip(self.expected_attempt)
                            .is_some_and(|(number, expected)| number != expected)
                        {
                            return Err(INVALID);
                        }
                    }
                    _ => return Err(INVALID),
                }
                self.result = Some(v);
                Ok(None)
            }
            _ => Err(INVALID),
        }
    }
    pub fn finish(self) -> Result<Value> {
        self.result.ok_or(INVALID)
    }
}
fn wire_options(v: &Value) -> Result<OpsOptions> {
    object(
        v,
        &[],
        &[
            "http_port",
            "dashboard_enabled",
            "start_on_login",
            "restart",
        ],
    )?;
    Ok(OpsOptions {
        http_port: optional(&v["http_port"], parse)?,
        dashboard_enabled: optional(&v["dashboard_enabled"], boolean)?,
        start_on_login: optional(&v["start_on_login"], boolean)?,
        restart: optional(&v["restart"], boolean)?,
    })
}
// serde_json::Value normally accepts duplicate object keys. Reject them at every
// nesting level before validating the protocol or retaining a plan.
struct Unique(Value);
impl<'de> Deserialize<'de> for Unique {
    fn deserialize<D: de::Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Unique;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("unique-key JSON")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_f64<E: de::Error>(self, _: f64) -> std::result::Result<Unique, E> {
                Err(E::custom("non-integer protocol number"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Unique, E> {
                Ok(Unique(v.into()))
            }
            fn visit_none<E: de::Error>(self) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Unique, E> {
                Ok(Unique(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut a: A,
            ) -> std::result::Result<Unique, A::Error> {
                let mut v = Vec::new();
                while let Some(Unique(item)) = a.next_element()? {
                    v.push(item);
                }
                Ok(Unique(v.into()))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut a: A,
            ) -> std::result::Result<Unique, A::Error> {
                let mut v = Map::new();
                while let Some((k, Unique(item))) = a.next_entry::<String, Unique>()? {
                    if v.insert(k, item).is_some() {
                        return Err(de::Error::custom("duplicate key"));
                    }
                }
                Ok(Unique(Value::Object(v)))
            }
        }
        d.deserialize_any(V)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn status(number: u32) -> Value {
        json!({
            "protocol_version": 1, "request_id": "request", "type": "result",
            "operation_id": "operation", "attempt": number, "phase": "finished",
            "executor": {"pid": null, "observation": "exited"},
            "execution_state": "finished", "outcome": "succeeded",
            "cancel_requested": false, "result": {"outcome": "succeeded"},
            "observation": {
                "support": "supported", "registration": "installed", "definition": "current",
                "manager_ownership": "owned", "manager": "active", "liveness": "live",
                "readiness": "not_probed", "endpoint": null, "maintenance": null,
                "resources": "present", "configuration": "valid"
            },
            "history": (1..=number).map(|attempt| json!({
                "attempt": attempt, "outcome": if attempt == number { "succeeded" } else { "failed" }
            })).collect::<Vec<_>>(),
            "recovery_actions": []
        })
    }

    fn parsed(request: &Value, response: &Value) -> Result<Value> {
        let mut stream = Stream::new(request)?;
        stream.line(&serde_json::to_vec(response).unwrap())?;
        stream.finish()
    }

    #[test]
    fn final_results_bind_attempt_without_accepted_and_status_can_read_latest() {
        for (action, expected) in [("apply", 1), ("resume", 3), ("cancel", 2)] {
            let mut request = json!({
                "protocol_version": 1, "request_id": "request", "action": action,
                "operation_id": "operation"
            });
            if action == "resume" {
                request["expected_attempt"] = (expected - 1).into();
            } else if action == "cancel" {
                request["attempt"] = expected.into();
            }
            // A duplicate resume or completed cancel returns status without progress.
            assert_eq!(
                parsed(&request, &status(expected)).unwrap(),
                status(expected)
            );
            let wrong = if expected == 1 { 2 } else { 1 };
            assert_eq!(parsed(&request, &status(wrong)), Err(INVALID));

            let rejected = json!({
                "protocol_version": 1, "request_id": "request", "type": "result",
                "outcome": "rejected", "error": {"code": "attempt_conflict"}
            });
            assert_eq!(parsed(&request, &rejected).unwrap(), rejected);

            if action != "cancel" {
                let mut checkpoint = json!({
                    "protocol_version": 1, "request_id": "request", "type": "result",
                    "operation_id": "operation", "attempt": expected,
                    "outcome": "uncertain", "error": {"code": "checkpoint_failed"},
                    "recovery_actions": ["operation_status"]
                });
                assert_eq!(parsed(&request, &checkpoint).unwrap(), checkpoint);
                checkpoint["attempt"] = wrong.into();
                assert_eq!(parsed(&request, &checkpoint), Err(INVALID));
            }
        }
        let request = json!({
            "protocol_version": 1, "request_id": "request", "action": "operation_status",
            "operation_id": "operation"
        });
        assert_eq!(parsed(&request, &status(4)).unwrap(), status(4));
    }

    #[test]
    fn inspection_and_operation_status_preserve_controller_maintenance_phases() {
        for (phase, expected) in [
            ("stopping", OpsMaintenancePhase::Stopping),
            ("stopped", OpsMaintenancePhase::Stopped),
            ("switching", OpsMaintenancePhase::Switching),
            ("database_ready", OpsMaintenancePhase::DatabaseReady),
        ] {
            let mut response = status(1);
            response["observation"]["maintenance"] = phase.into();
            let request = json!({
                "protocol_version": 1, "request_id": "request", "action": "operation_status",
                "operation_id": "operation"
            });
            let value = parsed(&request, &response).unwrap();
            let reply = operation(&value, "operation", 0).unwrap();
            assert_eq!(
                reply
                    .status
                    .unwrap()
                    .observation
                    .components
                    .unwrap()
                    .maintenance,
                Some(expected)
            );
            let inspection_response = json!({
                "protocol_version": 1, "request_id": "request", "type": "result",
                "outcome": "succeeded", "components": response["observation"]
            });
            let inspection_request = json!({
                "protocol_version": 1, "request_id": "request", "action": "inspect"
            });
            let value = parsed(&inspection_request, &inspection_response).unwrap();
            assert_eq!(
                inspection(&value, 0)
                    .unwrap()
                    .components
                    .unwrap()
                    .maintenance,
                Some(expected)
            );
        }
    }
}
