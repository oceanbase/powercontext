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

//! Memory-only replay correlation. Association is neither authorization nor execution readiness.
//! Production ingress, Core HTTP DTOs, prepared content, and Executor observations are outside this module.

mod carrier;
mod store;

pub use carrier::{EstablishmentReference, ReplayCarrier, ReplayHandle};
pub use store::{
    Association, CleanupResult, CloseResult, ReplayEpoch, ReplayLimits, ReplayStore, ReplaySupport,
    Resolution, TrustedContext,
};

/// Stable, content-free operation failures. HTTP mappings belong to later adapters.
#[derive(Clone, Copy, Debug, PartialEq, Eq, thiserror::Error)]
pub enum ReplayError {
    #[error("invalid_replay_carrier")]
    InvalidCarrier,
    #[error("unsupported_replay_carrier")]
    UnsupportedCarrier,
    #[error("replay_boundary_unavailable")]
    AssociationUnavailable,
    #[error("replay_boundary_exists")]
    BoundaryExists,
    #[error("replay_boundary_required")]
    CarrierRequired,
    #[error("replay_capacity_rejected")]
    CapacityRejected,
    #[error("replay_dependency_unavailable")]
    DependencyUnavailable,
    #[error("invalid_replay_configuration")]
    InvalidConfiguration,
    #[error("invalid_replay_cleanup_batch")]
    InvalidCleanupBatch,
}
