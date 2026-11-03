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

//! Operation availability requires discovery compatibility and explicit qualification evidence.
//! A failed discovery observation gates operations until a successful recheck; health is not proof.
use super::session::CompatibilityProfile;
use crate::{
    error::SafeError,
    transport::{
        ApiFailure, DiscoveryDocument,
        wire::{ContractVersion, ServerInfo},
    },
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use ts_rs::TS;

#[derive(Clone, Copy, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum DiscoveryStatus {
    Supported,
    Missing,
    Denied,
    Unavailable,
    Invalid,
    Incompatible,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum CompatibilityReason {
    ProductMismatch,
    SchemaVersion,
    ApiVersion,
    DiscoveryMissing,
    DiscoveryDenied,
    DiscoveryUnavailable,
    InvalidDiscovery,
    QualificationRequired,
    QualifiedProfile,
    FeatureMissing,
    FeatureVersion,
    OperationMissing,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum CompatibilityStatus {
    Qualified,
    Unsupported,
    Unverified,
}

#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct OperationCompatibility {
    pub status: CompatibilityStatus,
    pub reason: CompatibilityReason,
    pub qualification_id: Option<String>,
}

#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct DiscoveryResult {
    pub status: DiscoveryStatus,
    pub server: Option<ServerInfo>,
    pub error: Option<ApiFailure>,
    pub reason: Option<CompatibilityReason>,
    pub operations: BTreeMap<String, OperationCompatibility>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Requirements {
    api_major: i64,
    features: BTreeMap<String, ContractVersion>,
    operation_features: BTreeMap<String, Vec<String>>,
}

impl DiscoveryResult {
    /// Evidence is selected and checked by the native manager; discovery never creates qualification.
    pub fn assess(
        response: Result<DiscoveryDocument, ApiFailure>,
        qualification: Option<&CompatibilityProfile>,
    ) -> Self {
        let manifest: serde_json::Value =
            serde_json::from_str(include_str!("../transport/operations.json"))
                .expect("generated operation manifest");
        let requirements: Requirements = serde_json::from_value(manifest["discovery"].clone())
            .expect("generated discovery requirements");
        let (status, server, error, reason) = match response {
            Ok(DiscoveryDocument::UnsupportedProduct) => (
                DiscoveryStatus::Incompatible,
                None,
                None,
                Some(CompatibilityReason::ProductMismatch),
            ),
            Ok(DiscoveryDocument::Server(server)) => {
                let reason = if server.schema_version.major != 1 {
                    Some(CompatibilityReason::SchemaVersion)
                } else if server.api_contract_version.major != requirements.api_major {
                    Some(CompatibilityReason::ApiVersion)
                } else {
                    None
                };
                (
                    if reason.is_some() {
                        DiscoveryStatus::Incompatible
                    } else {
                        DiscoveryStatus::Supported
                    },
                    Some(server),
                    None,
                    reason,
                )
            }
            Err(error) => {
                let (status, reason) = match error.code {
                    SafeError::NotFound => (
                        DiscoveryStatus::Missing,
                        CompatibilityReason::DiscoveryMissing,
                    ),
                    SafeError::Unauthorized | SafeError::Forbidden => (
                        DiscoveryStatus::Denied,
                        CompatibilityReason::DiscoveryDenied,
                    ),
                    SafeError::Network
                    | SafeError::Tls
                    | SafeError::Redirect
                    | SafeError::Busy
                    | SafeError::Timeout
                    | SafeError::Server
                    | SafeError::RuntimeNotReady
                    | SafeError::AuthenticationUnavailable => (
                        DiscoveryStatus::Unavailable,
                        CompatibilityReason::DiscoveryUnavailable,
                    ),
                    _ => (
                        DiscoveryStatus::Invalid,
                        CompatibilityReason::InvalidDiscovery,
                    ),
                };
                (status, None, Some(error), Some(reason))
            }
        };
        let operations = requirements
            .operation_features
            .iter()
            .map(|(operation, features)| {
                let known = qualification.filter(|q| q.operations.contains(operation));
                let restriction = if status == DiscoveryStatus::Supported {
                    let server = server.as_ref().expect("supported discovery");
                    features.iter().find_map(|feature| {
                        let minimum = &requirements.features[feature];
                        match server.feature_contracts.get(feature) {
                            None => Some(CompatibilityReason::FeatureMissing),
                            Some(actual)
                                if actual.version.major != minimum.major
                                    || actual.version.minor < minimum.minor =>
                            {
                                Some(CompatibilityReason::FeatureVersion)
                            }
                            Some(actual) if !actual.operations.contains(operation) => {
                                Some(CompatibilityReason::OperationMissing)
                            }
                            _ => None,
                        }
                    })
                } else {
                    reason
                };
                let (status, reason) =
                    if status == DiscoveryStatus::Missing && operation == "get_server_info" {
                        (
                            CompatibilityStatus::Unsupported,
                            CompatibilityReason::DiscoveryMissing,
                        )
                    } else if status == DiscoveryStatus::Missing && known.is_some() {
                        (
                            CompatibilityStatus::Qualified,
                            CompatibilityReason::QualifiedProfile,
                        )
                    } else if let Some(restriction) = restriction {
                        (
                            if matches!(
                                status,
                                DiscoveryStatus::Supported | DiscoveryStatus::Incompatible
                            ) {
                                CompatibilityStatus::Unsupported
                            } else {
                                CompatibilityStatus::Unverified
                            },
                            restriction,
                        )
                    } else if known.is_some() {
                        (
                            CompatibilityStatus::Qualified,
                            CompatibilityReason::QualifiedProfile,
                        )
                    } else {
                        (
                            CompatibilityStatus::Unverified,
                            CompatibilityReason::QualificationRequired,
                        )
                    };
                (
                    operation.clone(),
                    OperationCompatibility {
                        status,
                        reason,
                        qualification_id: known.map(|q| q.id.clone()),
                    },
                )
            })
            .collect();
        Self {
            status,
            server,
            error,
            reason,
            operations,
        }
    }
    pub fn supported_operations(&self) -> Vec<String> {
        self.operations
            .iter()
            .filter(|(_, result)| result.status == CompatibilityStatus::Qualified)
            .map(|(operation, _)| operation.clone())
            .collect()
    }
}
