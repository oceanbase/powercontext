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

use powercontext_desktop::connections::{
    profiles::ProfileRepository,
    session::{CompatibilityProfile, ConnectionManager},
};
use sha2::{Digest, Sha256};

pub const PROFILE_ID: &str = "synthetic-native-fixture";

#[allow(dead_code)] // Each integration test is compiled as a separate crate.
pub fn discovery_document(id: &str) -> serde_json::Value {
    serde_json::json!({"schema_version":{"major":1,"minor":0},"product":"powercontext","server_id":id,
        "package_version":"synthetic","api_contract_version":{"major":1,"minor":4},"feature_contracts":{
            "connection.identity":{"version":{"major":1,"minor":0},"operations":["get_connection_identity"]},
            "access.principal":{"version":{"major":1,"minor":0},"operations":["get_access_principal"]},
            "scope.selection":{"version":{"major":1,"minor":0},"operations":["list_scopes","get_scope","get_default_scope"]},
            "memory.explicit":{"version":{"major":1,"minor":0},"operations":["remember_memory","search_memory"]}}})
}

pub fn connection_manager(profiles: ProfileRepository) -> ConnectionManager {
    let contract: serde_json::Value =
        serde_json::from_str(include_str!("../../src/transport/operations.json")).unwrap();
    let profile = CompatibilityProfile {
        id: PROFILE_ID.into(),
        server_commit: "synthetic-http-fixture-not-a-server-release".into(),
        contract_sha256: contract["contractSha256"].as_str().unwrap().into(),
        artifact_sha256: format!("{:x}", Sha256::digest(include_bytes!("mod.rs"))),
        authenticated_clients: Default::default(),
        operations: contract["operations"].as_object().unwrap().keys().cloned().collect(),
        evidence: "Synthetic HTTP fixtures in tests/memory.rs and tests/session.rs; not release qualification".into(),
    };
    ConnectionManager::with_compatibility(profiles, vec![profile])
}
