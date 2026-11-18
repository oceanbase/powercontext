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

use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Instant, SystemTime};

use super::{EstablishmentReference, ReplayError, ReplayHandle};

/// Explicit logical metadata budgets, including retained closed/expired records and every index.
#[derive(Clone, Copy, Debug)]
pub struct ReplayLimits {
    pub max_record_bytes: usize,
    pub max_principal_records: usize,
    pub max_principal_bytes: usize,
    pub max_total_records: usize,
    pub max_total_bytes: usize,
    pub cleanup_batch_limit: usize,
}

impl ReplayLimits {
    fn validate(self) -> Result<Self, ReplayError> {
        if [
            self.max_record_bytes,
            self.max_principal_records,
            self.max_principal_bytes,
            self.max_total_records,
            self.max_total_bytes,
            self.cleanup_batch_limit,
        ]
        .contains(&0)
            || self.max_record_bytes > self.max_principal_bytes
            || self.max_principal_bytes > self.max_total_bytes
            || self.max_principal_records > self.max_total_records
        {
            return Err(ReplayError::InvalidConfiguration);
        }
        Ok(self)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReplaySupport {
    ReliableGateway,
    UnsupportedHost,
    HostPreparation,
    Unavailable,
}

/// Inputs supplied by the authenticated G2 owner on each operation, never client identity assertions.
/// Raw host IDs are validated before normalization by the host boundary; no truncation is performed here.
#[derive(Clone, Copy, Debug)]
pub struct TrustedContext<'a> {
    pub deployment_id: &'a str,
    pub generation: &'a str,
    pub principal: &'a str,
    pub scope: &'a str,
    pub conversation: Option<&'a str>,
    pub support: ReplaySupport,
}

impl TrustedContext<'_> {
    fn partition(&self) -> Result<Option<Arc<Partition>>, ReplayError> {
        if !bounded_identity(self.deployment_id, 256)
            || !bounded_identity(self.generation, 256)
            || !bounded_identity(self.principal, 255)
            || !bounded_identity(self.scope, 256)
        {
            return Err(ReplayError::DependencyUnavailable);
        }
        let Some(conversation) = self.conversation else {
            return Ok(None);
        };
        if !bounded_identity(conversation, 525) {
            return Err(ReplayError::InvalidCarrier);
        }
        Ok(Some(Arc::new(Partition {
            deployment_id: self.deployment_id.into(),
            generation: self.generation.into(),
            principal: self.principal.into(),
            scope: self.scope.into(),
            conversation: conversation.into(),
        })))
    }

    fn supported_partition(&self) -> Result<Arc<Partition>, ReplayError> {
        let partition = self.partition()?.ok_or(ReplayError::InvalidCarrier)?;
        match self.support {
            ReplaySupport::ReliableGateway => Ok(partition),
            ReplaySupport::UnsupportedHost | ReplaySupport::HostPreparation => {
                Err(ReplayError::UnsupportedCarrier)
            }
            ReplaySupport::Unavailable => Err(ReplayError::DependencyUnavailable),
        }
    }
}

fn bounded_identity(value: &str, max_chars: usize) -> bool {
    !value.trim().is_empty()
        && value.chars().count() <= max_chars
        && !value.chars().any(char::is_control)
}

#[derive(Clone, PartialEq, Eq, Hash)]
struct Partition {
    deployment_id: Box<str>,
    generation: Box<str>,
    principal: Arc<str>,
    scope: Box<str>,
    conversation: Box<str>,
}

/// Generated exclusively by the store, distinct from a conversation label or establishment reference.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ReplayEpoch([u8; 32]);

/// Correlation metadata only. This value says nothing about current calls or execution/delivery.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Association {
    pub epoch: ReplayEpoch,
    pub handle: ReplayHandle,
    pub expires_at: Instant,
    pub compatibility: Box<[u8]>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Resolution {
    CheckOnly,
    Association(Association),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CloseResult {
    Closed,
    AlreadyClosed,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CleanupResult {
    pub examined: usize,
    pub removed: usize,
}

struct Record {
    partition: Arc<Partition>,
    association: Association,
    reference: EstablishmentReference,
    charged_bytes: usize,
    closed: bool,
}

#[derive(Default)]
struct Usage {
    records: usize,
    bytes: usize,
}

#[derive(Default)]
struct State {
    records: HashMap<ReplayEpoch, Record>,
    handles: HashMap<ReplayHandle, ReplayEpoch>,
    references: HashMap<(Arc<Partition>, EstablishmentReference), ReplayEpoch>,
    active: HashMap<Arc<Partition>, ReplayEpoch>,
    principals: HashMap<Arc<str>, Usage>,
    reclamation: BinaryHeap<Reverse<(Instant, ReplayEpoch)>>,
    reclaimed_through: Option<Instant>,
    bytes: usize,
}

/// Single-process authoritative correlation store. There is no persistence, eviction, or model I/O.
///
/// The owner must drive [`Self::cleanup`]; the store starts no background task and
/// [`Self::establish`] does not reclaim records. Closed or expired records remain charged against
/// entry and byte limits until cleanup removes them, although lookup rejects them immediately.
pub struct ReplayStore {
    limits: ReplayLimits,
    clock_origin: (Instant, SystemTime),
    state: Mutex<State>,
    #[cfg(test)]
    random_source: Option<TestRandomSource>,
}

#[cfg(test)]
type TestRandomSource = fn(&mut [u8]) -> Result<(), ReplayError>;

impl ReplayStore {
    /// Reference deadlines are mapped to the monotonic clock using the store's creation time.
    /// Hosts must generate references against a clock close to that time base.
    pub fn new(limits: ReplayLimits) -> Result<Self, ReplayError> {
        Ok(Self {
            limits: limits.validate()?,
            clock_origin: (Instant::now(), SystemTime::now()),
            state: Mutex::new(State::default()),
            #[cfg(test)]
            random_source: None,
        })
    }

    /// A retained reference conflicts within its trusted partition. Otherwise, a deadline that
    /// is not in the next 60 seconds results in [`ReplayError::AssociationUnavailable`]. Once
    /// reclaimed, the reference cannot be accepted again by this store, even with a time sample
    /// taken before cleanup. The reference deadline does not shorten recovery of an association.
    pub fn establish(
        &self,
        context: &TrustedContext<'_>,
        reference: EstablishmentReference,
        compatibility: &[u8],
        expires_at: Instant,
        now: Instant,
    ) -> Result<Association, ReplayError> {
        let mut partition = context.supported_partition()?;
        let mut state = self.lock()?;
        let now = now.max(state.reclaimed_through.unwrap_or(now));
        if expires_at <= now || compatibility.is_empty() {
            return Err(ReplayError::DependencyUnavailable);
        }
        if state
            .active
            .get(&partition)
            .and_then(|epoch| state.records.get(epoch))
            .is_some_and(|record| record.association.expires_at > now)
            || state
                .references
                .contains_key(&(partition.clone(), reference))
        {
            return Err(ReplayError::BoundaryExists);
        }
        let reclaim_at = expires_at.max(self.reference_deadline(reference, now)?);
        let charged_bytes =
            metadata_bytes(&partition, compatibility.len()).ok_or(ReplayError::CapacityRejected)?;
        let principal_usage = state.principals.get(&partition.principal);
        if charged_bytes > self.limits.max_record_bytes
            || state.records.len() >= self.limits.max_total_records
            || state
                .bytes
                .checked_add(charged_bytes)
                .is_none_or(|bytes| bytes > self.limits.max_total_bytes)
            || principal_usage
                .is_some_and(|usage| usage.records >= self.limits.max_principal_records)
            || principal_usage
                .map_or(0, |usage| usage.bytes)
                .checked_add(charged_bytes)
                .is_none_or(|bytes| bytes > self.limits.max_principal_bytes)
        {
            return Err(ReplayError::CapacityRejected);
        }
        if let Some((principal, _)) = state.principals.get_key_value(context.principal) {
            Arc::make_mut(&mut partition).principal = principal.clone();
        }
        let mut random = [0; 64];
        self.fill_random(&mut random)?;
        let epoch = ReplayEpoch(
            random[..32]
                .try_into()
                .map_err(|_| ReplayError::DependencyUnavailable)?,
        );
        let handle = ReplayHandle(
            random[32..]
                .try_into()
                .map_err(|_| ReplayError::DependencyUnavailable)?,
        );
        if state.records.contains_key(&epoch) || state.handles.contains_key(&handle) {
            return Err(ReplayError::DependencyUnavailable);
        }
        reserve_indexes(&mut state)?;
        let association = Association {
            epoch,
            handle,
            expires_at,
            compatibility: compatibility.into(),
        };
        state.records.insert(
            epoch,
            Record {
                partition: partition.clone(),
                association: association.clone(),
                reference,
                charged_bytes,
                closed: false,
            },
        );
        state.handles.insert(handle, epoch);
        state
            .references
            .insert((partition.clone(), reference), epoch);
        state.active.remove(&partition);
        state.active.insert(partition.clone(), epoch);
        state.reclamation.push(Reverse((reclaim_at, epoch)));
        let usage = state
            .principals
            .entry(partition.principal.clone())
            .or_default();
        usage.records += 1;
        usage.bytes += charged_bytes;
        state.bytes += charged_bytes;
        Ok(association)
    }

    pub fn resolve(
        &self,
        context: &TrustedContext<'_>,
        handle: Option<ReplayHandle>,
        now: Instant,
    ) -> Result<Resolution, ReplayError> {
        let partition = match handle {
            Some(_) => Some(context.supported_partition()?),
            None => context.partition()?,
        };
        if context.support == ReplaySupport::Unavailable {
            return Err(ReplayError::DependencyUnavailable);
        }
        let state = self.lock()?;
        let Some(partition) = partition else {
            return Ok(Resolution::CheckOnly);
        };
        match handle {
            None => {
                if state
                    .active
                    .get(&partition)
                    .and_then(|epoch| state.records.get(epoch))
                    .is_some_and(|record| record.association.expires_at > now)
                {
                    Err(ReplayError::CarrierRequired)
                } else {
                    Ok(Resolution::CheckOnly)
                }
            }
            Some(handle) => {
                let epoch = state
                    .handles
                    .get(&handle)
                    .ok_or(ReplayError::AssociationUnavailable)?;
                active_association(&state, &partition, epoch, now).map(Resolution::Association)
            }
        }
    }

    pub fn recover(
        &self,
        context: &TrustedContext<'_>,
        reference: EstablishmentReference,
        now: Instant,
    ) -> Result<Association, ReplayError> {
        let partition = context.supported_partition()?;
        let state = self.lock()?;
        let epoch = state
            .references
            .get(&(partition.clone(), reference))
            .ok_or(ReplayError::AssociationUnavailable)?;
        active_association(&state, &partition, epoch, now)
    }

    /// Trusted internal metadata closure; does not cancel or order any upstream work.
    pub fn close(
        &self,
        context: &TrustedContext<'_>,
        epoch: ReplayEpoch,
        now: Instant,
    ) -> Result<CloseResult, ReplayError> {
        let partition = context.supported_partition()?;
        let mut state = self.lock()?;
        let record = state
            .records
            .get_mut(&epoch)
            .ok_or(ReplayError::AssociationUnavailable)?;
        if record.partition != partition || record.association.expires_at <= now {
            return Err(ReplayError::AssociationUnavailable);
        }
        if record.closed {
            return Ok(CloseResult::AlreadyClosed);
        }
        record.closed = true;
        if state.active.get(&partition) == Some(&epoch) {
            state.active.remove(&partition);
        }
        Ok(CloseResult::Closed)
    }

    /// Reclaims expired records and their indexes, releasing entry and byte charges.
    ///
    /// The store owner must drive cleanup; it is not scheduled automatically. Records are eligible
    /// for removal at or after the later of their epoch and reference deadlines, so an old
    /// reference cannot establish another epoch once its index is removed.
    /// Examines at most `min(batch, cleanup_batch_limit)` entries, including a future head.
    pub fn cleanup(&self, now: Instant, batch: usize) -> Result<CleanupResult, ReplayError> {
        if batch == 0 {
            return Err(ReplayError::InvalidCleanupBatch);
        }
        let mut state = self.lock()?;
        let mut result = CleanupResult {
            examined: 0,
            removed: 0,
        };
        while result.examined < batch.min(self.limits.cleanup_batch_limit) {
            let Some(&Reverse((reclaim_at, epoch))) = state.reclamation.peek() else {
                break;
            };
            result.examined += 1;
            if reclaim_at > now {
                break;
            }
            state.reclamation.pop();
            let Some(record) = state.records.remove(&epoch) else {
                continue;
            };
            state.handles.remove(&record.association.handle);
            state
                .references
                .remove(&(record.partition.clone(), record.reference));
            if state.active.get(&record.partition) == Some(&epoch) {
                state.active.remove(&record.partition);
            }
            if let Some(usage) = state.principals.get_mut(&record.partition.principal) {
                usage.records -= 1;
                usage.bytes -= record.charged_bytes;
                if usage.records == 0 {
                    state.principals.remove(&record.partition.principal);
                }
            }
            state.bytes -= record.charged_bytes;
            state.reclaimed_through = state.reclaimed_through.max(Some(reclaim_at));
            result.removed += 1;
        }
        Ok(result)
    }

    fn reference_deadline(
        &self,
        reference: EstablishmentReference,
        now: Instant,
    ) -> Result<Instant, ReplayError> {
        let (origin, wall_time) = self.clock_origin;
        reference
            .establishment_deadline()
            .and_then(|deadline| deadline.duration_since(wall_time).ok())
            .and_then(|offset| origin.checked_add(offset))
            .filter(|deadline| {
                *deadline > now && *deadline - now <= EstablishmentReference::MAX_WINDOW
            })
            .ok_or(ReplayError::AssociationUnavailable)
    }

    fn lock(&self) -> Result<MutexGuard<'_, State>, ReplayError> {
        self.state
            .lock()
            .map_err(|_| ReplayError::DependencyUnavailable)
    }

    fn fill_random(&self, bytes: &mut [u8]) -> Result<(), ReplayError> {
        #[cfg(test)]
        if let Some(source) = self.random_source {
            return source(bytes);
        }
        getrandom::getrandom(bytes).map_err(|_| ReplayError::DependencyUnavailable)
    }
}

fn active_association(
    state: &State,
    partition: &Arc<Partition>,
    epoch: &ReplayEpoch,
    now: Instant,
) -> Result<Association, ReplayError> {
    let record = state
        .records
        .get(epoch)
        .ok_or(ReplayError::AssociationUnavailable)?;
    if record.partition != *partition
        || record.closed
        || record.association.expires_at <= now
        || state.active.get(partition) != Some(epoch)
    {
        return Err(ReplayError::AssociationUnavailable);
    }
    Ok(record.association.clone())
}

fn reserve_indexes(state: &mut State) -> Result<(), ReplayError> {
    state
        .records
        .try_reserve(1)
        .map_err(|_| ReplayError::CapacityRejected)?;
    state
        .handles
        .try_reserve(1)
        .map_err(|_| ReplayError::CapacityRejected)?;
    state
        .references
        .try_reserve(1)
        .map_err(|_| ReplayError::CapacityRejected)?;
    state
        .active
        .try_reserve(1)
        .map_err(|_| ReplayError::CapacityRejected)?;
    state
        .principals
        .try_reserve(1)
        .map_err(|_| ReplayError::CapacityRejected)?;
    state
        .reclamation
        .try_reserve(1)
        .map_err(|_| ReplayError::CapacityRejected)?;
    Ok(())
}

/// Logical fixed reservations: record 256, partition/principal 192, handle index 128,
/// reference index 160, active index 128, reclamation index 128, accounting 32 bytes.
fn metadata_bytes(partition: &Partition, compatibility_bytes: usize) -> Option<usize> {
    let mut bytes = 1024usize;
    for length in [
        partition.deployment_id.len(),
        partition.generation.len(),
        partition.principal.len(),
        partition.scope.len(),
        partition.conversation.len(),
        compatibility_bytes,
    ] {
        bytes = bytes.checked_add(length)?;
    }
    Some(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn unavailable(_: &mut [u8]) -> Result<(), ReplayError> {
        Err(ReplayError::DependencyUnavailable)
    }

    fn initial(bytes: &mut [u8]) -> Result<(), ReplayError> {
        bytes.fill(7);
        Ok(())
    }

    fn epoch_collision(bytes: &mut [u8]) -> Result<(), ReplayError> {
        bytes.fill(8);
        bytes[..32].fill(7);
        Ok(())
    }

    fn handle_collision(bytes: &mut [u8]) -> Result<(), ReplayError> {
        bytes.fill(8);
        bytes[32..].fill(7);
        Ok(())
    }

    #[test]
    fn reference_deadline_bounds_establishment_and_reclamation_at_exact_clock_boundaries() {
        use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
        use std::time::UNIX_EPOCH;

        let mut store = ReplayStore::new(ReplayLimits {
            max_record_bytes: 4096,
            max_principal_records: 1,
            max_principal_bytes: 4096,
            max_total_records: 1,
            max_total_bytes: 4096,
            cleanup_batch_limit: 1,
        })
        .unwrap();
        let now = Instant::now();
        store.clock_origin = (now, UNIX_EPOCH + Duration::from_secs(100));
        let reference_with_deadline = |millis: u64| {
            let mut bytes = [7; 32];
            bytes[..8].copy_from_slice(&millis.to_be_bytes());
            EstablishmentReference::parse(&URL_SAFE_NO_PAD.encode(bytes)).unwrap()
        };
        let context = TrustedContext {
            deployment_id: "deployment",
            generation: "generation",
            principal: "principal",
            scope: "scope",
            conversation: Some("conversation"),
            support: ReplaySupport::ReliableGateway,
        };
        let epoch_deadline = now + Duration::from_secs(5);
        let reference = reference_with_deadline(160_000);
        for invalid in [
            reference_with_deadline(99_999),
            reference_with_deadline(100_000),
            reference_with_deadline(160_001),
            reference_with_deadline(u64::MAX),
        ] {
            assert_eq!(
                store.establish(&context, invalid, b"binding", epoch_deadline, now),
                Err(ReplayError::AssociationUnavailable)
            );
        }
        store
            .establish(&context, reference, b"binding", epoch_deadline, now)
            .unwrap();
        let reclaim_at = now + Duration::from_secs(60);
        let delayed_sample = reclaim_at - Duration::from_nanos(1);
        assert_eq!(store.cleanup(delayed_sample, 1).unwrap().removed, 0);
        assert_eq!(store.cleanup(reclaim_at, 1).unwrap().removed, 1);
        for sample in [delayed_sample, reclaim_at] {
            assert_eq!(
                store.establish(
                    &context,
                    reference,
                    b"changed",
                    reclaim_at + Duration::from_secs(5),
                    sample,
                ),
                Err(ReplayError::AssociationUnavailable)
            );
        }
        assert!(store
            .establish(
                &context,
                reference_with_deadline(220_000),
                b"changed",
                reclaim_at + Duration::from_secs(5),
                reclaim_at,
            )
            .is_ok());
    }

    #[test]
    fn random_failure_or_either_collision_leaves_existing_association_and_capacity_unchanged() {
        for source in [unavailable, epoch_collision, handle_collision] {
            let mut store = ReplayStore::new(ReplayLimits {
                max_record_bytes: 4096,
                max_principal_records: 2,
                max_principal_bytes: 8192,
                max_total_records: 2,
                max_total_bytes: 8192,
                cleanup_batch_limit: 2,
            })
            .unwrap();
            store.random_source = Some(initial);
            let context = TrustedContext {
                deployment_id: "deployment",
                generation: "generation",
                principal: "principal",
                scope: "scope",
                conversation: Some("first"),
                support: ReplaySupport::ReliableGateway,
            };
            let now = Instant::now();
            let deadline = now + Duration::from_secs(60);
            let reference = EstablishmentReference::generate().unwrap();
            let first = store
                .establish(&context, reference, b"binding", deadline, now)
                .unwrap();
            let next = TrustedContext {
                conversation: Some("second"),
                ..context
            };
            store.random_source = Some(source);
            assert_eq!(
                store.establish(&next, reference, b"binding", deadline, now),
                Err(ReplayError::DependencyUnavailable)
            );
            assert_eq!(store.resolve(&next, None, now), Ok(Resolution::CheckOnly));
            assert_eq!(
                store.recover(&next, reference, now),
                Err(ReplayError::AssociationUnavailable)
            );
            assert_eq!(store.recover(&context, reference, now), Ok(first));
            store.random_source = None;
            assert!(store
                .establish(&next, reference, b"binding", deadline, now)
                .is_ok());
        }
    }
}
