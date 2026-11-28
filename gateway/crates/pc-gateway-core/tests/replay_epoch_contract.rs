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

use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use pc_gateway_core::replay_epoch::CloseResult;
use pc_gateway_core::replay_epoch::{
    EstablishmentReference, ReplayCarrier, ReplayError, ReplayHandle,
};
use pc_gateway_core::replay_epoch::{
    ReplayLimits, ReplayStore, ReplaySupport, Resolution, TrustedContext,
};
use std::sync::{Arc, Barrier};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const REFERENCE: &str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
const HANDLE: &str = "v1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

#[test]
fn control_headers_distinguish_absence_establishment_and_continuation() {
    assert_eq!(
        ReplayCarrier::parse(&[], &[], None),
        Ok(ReplayCarrier::Absent)
    );
    assert_eq!(
        ReplayCarrier::parse(&["new"], &[REFERENCE], Some("powercontext:chat")),
        Ok(ReplayCarrier::Establish(
            EstablishmentReference::parse(REFERENCE).unwrap()
        ))
    );
    assert_eq!(
        ReplayCarrier::parse(&[HANDLE], &[], Some("powercontext:chat")),
        Ok(ReplayCarrier::Continue(
            ReplayHandle::parse(HANDLE).unwrap()
        ))
    );
}

#[test]
fn invalid_controls_never_select_check_only() {
    for (replay, reference, conversation) in [
        (vec![""], vec![], Some("chat")),
        (vec!["new", "new"], vec![REFERENCE], Some("chat")),
        (vec!["new,new"], vec![REFERENCE], Some("chat")),
        (vec!["new"], vec![REFERENCE, REFERENCE], Some("chat")),
        (vec!["new"], vec![], Some("chat")),
        (vec![], vec![REFERENCE], Some("chat")),
        (vec![HANDLE], vec![REFERENCE], Some("chat")),
        (vec!["new"], vec![REFERENCE], None),
        (vec![HANDLE], vec![], None),
        (vec!["new"], vec![REFERENCE], Some("")),
        (vec!["New"], vec![REFERENCE], Some("chat")),
        (vec![" new"], vec![REFERENCE], Some("chat")),
    ] {
        assert_eq!(
            ReplayCarrier::parse(&replay, &reference, conversation),
            Err(ReplayError::InvalidCarrier)
        );
    }
    for value in [
        "v0.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "v01.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "v1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB",
        "v1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "v1.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/",
        "v1.short",
    ] {
        assert_eq!(ReplayHandle::parse(value), Err(ReplayError::InvalidCarrier));
    }
    for version in ["2", "10", "9999999999999999999999999999999999999999"] {
        assert_eq!(
            ReplayHandle::parse(&format!("v{version}.{REFERENCE}")),
            Err(ReplayError::UnsupportedCarrier)
        );
    }
    assert_eq!(
        ReplayHandle::parse("v2.malformed"),
        Err(ReplayError::InvalidCarrier)
    );
    for reference in [
        "",
        "short",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAB",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "AAAAAAAAAAAAAAAAAAAA,AAAAAAAAAAAAAAAAAAAAAA",
    ] {
        assert_eq!(
            ReplayCarrier::parse(&["new"], &[reference], Some("chat")),
            Err(ReplayError::InvalidCarrier)
        );
    }
}

fn limits() -> ReplayLimits {
    ReplayLimits {
        max_record_bytes: 16_384,
        max_principal_records: 100,
        max_principal_bytes: 1_638_400,
        max_total_records: 1000,
        max_total_bytes: 16_384_000,
        cleanup_batch_limit: 8,
    }
}

fn context() -> TrustedContext<'static> {
    TrustedContext {
        deployment_id: "deployment",
        generation: "generation",
        principal: "owner",
        scope: "scope",
        conversation: Some("powercontext:chat"),
        support: ReplaySupport::ReliableGateway,
    }
}

fn reference_at(elapsed: Duration) -> EstablishmentReference {
    let deadline = SystemTime::now() + elapsed + Duration::from_secs(10);
    let millis = u64::try_from(deadline.duration_since(UNIX_EPOCH).unwrap().as_millis()).unwrap();
    let mut bytes = [0; 32];
    bytes[..8].copy_from_slice(&millis.to_be_bytes());
    EstablishmentReference::parse(&URL_SAFE_NO_PAD.encode(bytes)).unwrap()
}

#[test]
fn lost_response_recovery_restores_only_the_original_stable_association() {
    let store = ReplayStore::new(limits()).unwrap();
    let context = context();
    let now = Instant::now();
    let deadline = now + Duration::from_secs(60);
    let reference = EstablishmentReference::generate().unwrap();
    assert_eq!(
        store.resolve(&context, None, now),
        Ok(Resolution::CheckOnly)
    );
    let established = store
        .establish(
            &context,
            reference,
            b"target/config/renderer",
            deadline,
            now,
        )
        .unwrap();
    assert_eq!(
        store.resolve(&context, None, now),
        Err(ReplayError::CarrierRequired)
    );
    assert_eq!(
        store.establish(&context, reference, b"changed", deadline, now),
        Err(ReplayError::BoundaryExists)
    );
    let recovery_at = now + Duration::from_secs(40);
    assert_eq!(store.cleanup(recovery_at, 1).unwrap().removed, 0);
    let recovered = store.recover(&context, reference, recovery_at).unwrap();
    assert_eq!(recovered, established);
    assert_eq!(recovered.expires_at, deadline);
    assert_eq!(recovered.compatibility.as_ref(), b"target/config/renderer");
    let returned = ReplayHandle::parse(&recovered.handle.to_header_value()).unwrap();
    assert_eq!(
        store.resolve(&context, Some(returned), recovery_at),
        Ok(Resolution::Association(established))
    );
    assert_eq!(
        store.recover(&context, reference, deadline),
        Err(ReplayError::AssociationUnavailable)
    );
}

#[test]
fn exact_close_and_old_cleanup_cannot_affect_a_replacement_boundary() {
    let store = ReplayStore::new(limits()).unwrap();
    let context = context();
    let now = Instant::now();
    let deadline = now + Duration::from_secs(60);
    let old_reference = EstablishmentReference::generate().unwrap();
    let old = store
        .establish(&context, old_reference, b"binding", deadline, now)
        .unwrap();
    assert_eq!(
        store.close(&context, old.epoch, now),
        Ok(CloseResult::Closed)
    );
    assert_eq!(
        store.resolve(&context, Some(old.handle), now),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        store.recover(&context, old_reference, now),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        store.establish(&context, old_reference, b"binding", deadline, now),
        Err(ReplayError::BoundaryExists)
    );
    let new_reference = EstablishmentReference::generate().unwrap();
    let new = store
        .establish(
            &context,
            new_reference,
            b"new binding",
            deadline + Duration::from_secs(60),
            now,
        )
        .unwrap();
    assert_ne!(new.epoch, old.epoch);
    assert_ne!(new.handle, old.handle);
    assert_eq!(
        store.close(&context, old.epoch, now),
        Ok(CloseResult::AlreadyClosed)
    );
    assert_eq!(store.cleanup(deadline, 1).unwrap().removed, 1);
    assert_eq!(
        store.close(&context, old.epoch, deadline),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        store.resolve(&context, None, deadline),
        Err(ReplayError::CarrierRequired)
    );
    assert_eq!(store.recover(&context, new_reference, deadline), Ok(new));
    assert_eq!(
        store.cleanup(deadline, 0),
        Err(ReplayError::InvalidCleanupBatch)
    );
}

#[test]
fn all_trusted_partition_components_are_required_for_lookup_and_close() {
    let store = ReplayStore::new(limits()).unwrap();
    let context = context();
    let now = Instant::now();
    let reference = EstablishmentReference::generate().unwrap();
    let association = store
        .establish(
            &context,
            reference,
            b"frozen",
            now + Duration::from_secs(60),
            now,
        )
        .unwrap();
    for changed in [
        TrustedContext {
            deployment_id: "other",
            ..context
        },
        TrustedContext {
            generation: "other",
            ..context
        },
        TrustedContext {
            principal: "other",
            ..context
        },
        TrustedContext {
            scope: "other",
            ..context
        },
        TrustedContext {
            conversation: Some("other"),
            ..context
        },
    ] {
        assert_eq!(
            store.resolve(&changed, Some(association.handle), now),
            Err(ReplayError::AssociationUnavailable)
        );
        assert_eq!(
            store.recover(&changed, reference, now),
            Err(ReplayError::AssociationUnavailable)
        );
        assert_eq!(
            store.close(&changed, association.epoch, now),
            Err(ReplayError::AssociationUnavailable)
        );
        assert_eq!(
            store.resolve(&changed, None, now),
            Ok(Resolution::CheckOnly)
        );
    }
    let other = TrustedContext {
        conversation: Some("other"),
        ..context
    };
    let other_association = store
        .establish(
            &other,
            reference,
            b"other",
            now + Duration::from_secs(60),
            now,
        )
        .unwrap();
    assert_eq!(
        store.close(&context, other_association.epoch, now),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(store.recover(&context, reference, now), Ok(association));
    assert_eq!(store.recover(&other, reference, now), Ok(other_association));
}

#[test]
fn synchronized_establishment_has_one_creator_for_same_or_different_references() {
    for same_reference in [true, false] {
        let store = Arc::new(ReplayStore::new(limits()).unwrap());
        let barrier = Arc::new(Barrier::new(12));
        let now = Instant::now();
        let shared_reference = EstablishmentReference::generate().unwrap();
        let callers: Vec<_> = (0..12)
            .map(|_| {
                let store = store.clone();
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    let reference = if same_reference {
                        shared_reference
                    } else {
                        EstablishmentReference::generate().unwrap()
                    };
                    barrier.wait();
                    store.establish(
                        &context(),
                        reference,
                        b"binding",
                        now + Duration::from_secs(60),
                        now,
                    )
                })
            })
            .collect();
        let results: Vec<_> = callers
            .into_iter()
            .map(|caller| caller.join().unwrap())
            .collect();
        assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
        assert_eq!(
            results
                .iter()
                .filter(|result| **result == Err(ReplayError::BoundaryExists))
                .count(),
            11
        );
    }
}

#[test]
fn fresh_store_and_unknown_handles_never_establish_an_epoch() {
    let store = ReplayStore::new(limits()).unwrap();
    let now = Instant::now();
    let reference = EstablishmentReference::generate().unwrap();
    let old = store
        .establish(
            &context(),
            reference,
            b"frozen",
            now + Duration::from_secs(60),
            now,
        )
        .unwrap();
    let replacement = ReplayStore::new(limits()).unwrap();
    assert_eq!(
        replacement.resolve(&context(), Some(old.handle), now),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        replacement.recover(&context(), reference, now),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        replacement.close(&context(), old.epoch, now),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        replacement.resolve(&context(), None, now),
        Ok(Resolution::CheckOnly)
    );
    assert_eq!(
        store.resolve(&context(), Some(ReplayHandle::parse(HANDLE).unwrap()), now),
        Err(ReplayError::AssociationUnavailable)
    );
}

#[test]
fn expiry_replacement_retains_old_reference_and_budget_until_bounded_cleanup() {
    let store = ReplayStore::new(ReplayLimits {
        max_principal_records: 2,
        max_total_records: 2,
        ..limits()
    })
    .unwrap();
    let now = Instant::now();
    let lifetime = Duration::from_secs(40);
    let deadline = now + lifetime;
    let reference = EstablishmentReference::generate().unwrap();
    let old = store
        .establish(&context(), reference, b"old", deadline, now)
        .unwrap();
    assert_eq!(
        store.recover(&context(), reference, deadline),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        store.resolve(&context(), Some(old.handle), deadline),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        store.close(&context(), old.epoch, deadline),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        store.establish(
            &context(),
            reference,
            b"old",
            deadline + Duration::from_secs(10),
            deadline
        ),
        Err(ReplayError::BoundaryExists)
    );
    let new_reference = reference_at(lifetime);
    let new = store
        .establish(
            &context(),
            new_reference,
            b"new",
            deadline + Duration::from_secs(10),
            deadline,
        )
        .unwrap();
    let other = TrustedContext {
        conversation: Some("another"),
        ..context()
    };
    assert_eq!(
        store.establish(
            &other,
            new_reference,
            b"other",
            deadline + Duration::from_secs(10),
            deadline
        ),
        Err(ReplayError::CapacityRejected)
    );
    assert_eq!(store.cleanup(deadline, 1).unwrap().removed, 1);
    assert_eq!(
        store.resolve(&context(), None, deadline),
        Err(ReplayError::CarrierRequired)
    );
    assert_eq!(store.recover(&context(), new_reference, deadline), Ok(new));
    assert!(store
        .establish(
            &other,
            new_reference,
            b"other",
            deadline + Duration::from_secs(10),
            deadline
        )
        .is_ok());
}

#[test]
fn reclaimed_establishment_reference_cannot_be_bound_again() {
    let store = ReplayStore::new(ReplayLimits {
        max_principal_records: 1,
        max_total_records: 1,
        ..limits()
    })
    .unwrap();
    let now = Instant::now();
    let epoch_deadline = now + Duration::from_secs(5);
    let reference = EstablishmentReference::generate().unwrap();
    store
        .establish(&context(), reference, b"original", epoch_deadline, now)
        .unwrap();
    assert_eq!(
        store.recover(&context(), reference, epoch_deadline),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(store.cleanup(epoch_deadline, 1).unwrap().removed, 0);

    let elapsed = Duration::from_secs(40);
    let reclaim_at = now + elapsed;
    let next_deadline = reclaim_at + Duration::from_secs(20);
    let fresh_reference = reference_at(elapsed);
    assert_eq!(
        store.establish(
            &context(),
            fresh_reference,
            b"different",
            next_deadline,
            epoch_deadline,
        ),
        Err(ReplayError::CapacityRejected)
    );
    assert_eq!(store.cleanup(reclaim_at, 1).unwrap().removed, 1);
    assert_eq!(
        store.establish(
            &context(),
            reference,
            b"different",
            next_deadline,
            reclaim_at
        ),
        Err(ReplayError::AssociationUnavailable)
    );
    let replacement = store
        .establish(
            &context(),
            fresh_reference,
            b"different",
            next_deadline,
            reclaim_at,
        )
        .unwrap();
    assert_eq!(
        store.recover(&context(), reference, reclaim_at),
        Err(ReplayError::AssociationUnavailable)
    );
    assert_eq!(
        store.recover(&context(), fresh_reference, reclaim_at),
        Ok(replacement)
    );
}

#[test]
fn principal_and_global_entry_limits_count_closed_records_until_cleanup() {
    let store = ReplayStore::new(ReplayLimits {
        max_principal_records: 1,
        max_total_records: 2,
        ..limits()
    })
    .unwrap();
    let now = Instant::now();
    let lifetime = Duration::from_secs(40);
    let deadline = now + lifetime;
    let reference = EstablishmentReference::generate().unwrap();
    let first = store
        .establish(&context(), reference, b"frozen", deadline, now)
        .unwrap();
    let another_scope = TrustedContext {
        scope: "another scope",
        ..context()
    };
    let another_principal = TrustedContext {
        principal: "another owner",
        ..context()
    };
    assert_eq!(
        store.establish(&another_scope, reference, b"binding", deadline, now),
        Err(ReplayError::CapacityRejected)
    );
    assert!(store
        .establish(&another_principal, reference, b"binding", deadline, now)
        .is_ok());
    assert_eq!(
        store.close(&context(), first.epoch, now),
        Ok(CloseResult::Closed)
    );
    assert_eq!(
        store.establish(
            &context(),
            EstablishmentReference::generate().unwrap(),
            b"binding",
            deadline,
            now
        ),
        Err(ReplayError::CapacityRejected)
    );
    let third = TrustedContext {
        principal: "third owner",
        ..context()
    };
    assert_eq!(
        store.establish(&third, reference, b"binding", deadline, now),
        Err(ReplayError::CapacityRejected)
    );
    assert_eq!(store.cleanup(deadline, 100).unwrap().removed, 2);
    assert!(store
        .establish(
            &context(),
            reference_at(lifetime),
            b"binding",
            deadline + Duration::from_secs(10),
            deadline
        )
        .is_ok());
}

#[test]
fn byte_budgets_charge_identifiers_bindings_and_indexes_even_at_low_record_counts() {
    let now = Instant::now();
    let lifetime = Duration::from_secs(60);
    let deadline = now + lifetime;
    let reference = EstablishmentReference::generate().unwrap();
    let small = ReplayLimits {
        max_record_bytes: 1200,
        max_principal_bytes: 2400,
        max_total_bytes: 3600,
        ..limits()
    };
    let store = ReplayStore::new(small).unwrap();
    let first = store
        .establish(&context(), reference, b"binding", deadline, now)
        .unwrap();
    let long_conversation = format!("claude-code:{} {}", "s".repeat(256), "a".repeat(256));
    let long = TrustedContext {
        conversation: Some(&long_conversation),
        ..context()
    };
    assert_eq!(
        store.establish(&long, reference, b"binding", deadline, now),
        Err(ReplayError::CapacityRejected)
    );
    let other = TrustedContext {
        conversation: Some("another"),
        ..context()
    };
    assert_eq!(
        store.establish(&other, reference, &[0; 256], deadline, now),
        Err(ReplayError::CapacityRejected)
    );
    assert_eq!(store.recover(&context(), reference, now), Ok(first));
    assert!(store
        .establish(&other, reference, b"binding", deadline, now)
        .is_ok());
    let third = TrustedContext {
        conversation: Some("third"),
        ..context()
    };
    assert_eq!(
        store.establish(&third, reference, b"binding", deadline, now),
        Err(ReplayError::CapacityRejected)
    );
    let different_principal = TrustedContext {
        principal: "other owner",
        ..third
    };
    assert!(store
        .establish(&different_principal, reference, b"binding", deadline, now)
        .is_ok());
    let last = TrustedContext {
        principal: "last owner",
        ..third
    };
    assert_eq!(
        store.establish(&last, reference, b"binding", deadline, now),
        Err(ReplayError::CapacityRejected)
    );
    assert_eq!(store.cleanup(deadline, 100).unwrap().removed, 3);
    assert!(store
        .establish(
            &context(),
            reference_at(lifetime),
            b"binding",
            deadline + Duration::from_secs(60),
            deadline
        )
        .is_ok());
}

#[test]
fn composite_conversation_identity_is_preserved_without_truncation() {
    let store = ReplayStore::new(limits()).unwrap();
    let now = Instant::now();
    let first_id = format!("claude-code:{} {}", "s".repeat(256), "a".repeat(256));
    let second_id = format!("claude-code:{} {}b", "s".repeat(256), "a".repeat(255));
    let first_context = TrustedContext {
        conversation: Some(&first_id),
        ..context()
    };
    let second_context = TrustedContext {
        conversation: Some(&second_id),
        ..context()
    };
    let reference = EstablishmentReference::generate().unwrap();
    let first = store
        .establish(
            &first_context,
            reference,
            b"frozen",
            now + Duration::from_secs(60),
            now,
        )
        .unwrap();
    assert_eq!(
        store.resolve(&second_context, Some(first.handle), now),
        Err(ReplayError::AssociationUnavailable)
    );
    let second = store
        .establish(
            &second_context,
            reference,
            b"other",
            now + Duration::from_secs(60),
            now,
        )
        .unwrap();
    assert_eq!(store.recover(&first_context, reference, now), Ok(first));
    assert_eq!(store.recover(&second_context, reference, now), Ok(second));
}

#[test]
fn closing_and_expiry_do_not_reclaim_byte_budget_before_cleanup() {
    let store = ReplayStore::new(ReplayLimits {
        max_record_bytes: 1200,
        max_principal_bytes: 1200,
        max_total_bytes: 2400,
        ..limits()
    })
    .unwrap();
    let now = Instant::now();
    let lifetime = Duration::from_secs(60);
    let deadline = now + lifetime;
    let reference = EstablishmentReference::generate().unwrap();
    let first = store
        .establish(&context(), reference, b"binding", deadline, now)
        .unwrap();
    assert_eq!(
        store.close(&context(), first.epoch, now),
        Ok(CloseResult::Closed)
    );
    assert_eq!(
        store.establish(
            &context(),
            EstablishmentReference::generate().unwrap(),
            b"binding",
            deadline,
            now,
        ),
        Err(ReplayError::CapacityRejected)
    );
    let next_deadline = deadline + lifetime;
    let fresh_reference = reference_at(lifetime);
    assert_eq!(
        store.establish(
            &context(),
            fresh_reference,
            b"binding",
            next_deadline,
            deadline
        ),
        Err(ReplayError::CapacityRejected)
    );
    assert_eq!(store.cleanup(deadline, 1).unwrap().removed, 1);
    let replacement = store
        .establish(
            &context(),
            fresh_reference,
            b"binding",
            next_deadline,
            deadline,
        )
        .unwrap();
    assert_eq!(
        store.recover(&context(), fresh_reference, deadline),
        Ok(replacement)
    );
}

#[test]
fn unicode_identity_limits_count_characters_but_capacity_charges_utf8_bytes() {
    let store = ReplayStore::new(ReplayLimits {
        max_record_bytes: 1600,
        ..limits()
    })
    .unwrap();
    let now = Instant::now();
    let deadline = now + Duration::from_secs(60);
    let reference = EstablishmentReference::generate().unwrap();
    let principal = "界".repeat(255);
    let large = TrustedContext {
        principal: &principal,
        ..context()
    };
    assert_eq!(
        store.establish(&large, reference, b"binding", deadline, now),
        Err(ReplayError::CapacityRejected)
    );
    let overlong = "界".repeat(256);
    let invalid = TrustedContext {
        principal: &overlong,
        ..context()
    };
    assert_eq!(
        store.establish(&invalid, reference, b"binding", deadline, now),
        Err(ReplayError::DependencyUnavailable)
    );
    let supported = TrustedContext {
        scope: "项目",
        conversation: Some("可信来源:会话 代理"),
        ..context()
    };
    assert!(store
        .establish(&supported, reference, b"binding", deadline, now)
        .is_ok());
}

#[test]
fn cleanup_caps_examined_entries_and_reclaims_all_indexes_at_scale() {
    let store = ReplayStore::new(ReplayLimits {
        cleanup_batch_limit: 3,
        max_principal_records: 40,
        max_total_records: 40,
        ..limits()
    })
    .unwrap();
    let now = Instant::now();
    let lifetime = Duration::from_secs(60);
    let deadline = now + lifetime;
    let reference = EstablishmentReference::generate().unwrap();
    for index in 0..40 {
        let conversation = format!("powercontext:{index}");
        let context = TrustedContext {
            conversation: Some(&conversation),
            ..context()
        };
        store
            .establish(&context, reference, b"binding", deadline, now)
            .unwrap();
    }
    let before = store.cleanup(now, usize::MAX).unwrap();
    assert_eq!(before.examined, 1);
    assert_eq!(before.removed, 0);
    let mut removed = 0;
    while removed < 40 {
        let batch = store.cleanup(deadline, usize::MAX).unwrap();
        assert!(batch.examined <= 3);
        assert!(batch.removed > 0);
        removed += batch.removed;
    }
    assert_eq!(removed, 40);
    assert_eq!(store.cleanup(deadline, 1).unwrap().examined, 0);
    let fresh_reference = reference_at(lifetime);
    for index in 0..40 {
        let conversation = format!("powercontext:{index}");
        let context = TrustedContext {
            conversation: Some(&conversation),
            ..context()
        };
        assert_eq!(
            store.recover(&context, reference, deadline),
            Err(ReplayError::AssociationUnavailable)
        );
        store
            .establish(
                &context,
                fresh_reference,
                b"binding",
                deadline + Duration::from_secs(60),
                deadline,
            )
            .unwrap();
    }
}

#[test]
fn trusted_failures_and_unsupported_hosts_preserve_an_existing_epoch() {
    let store = ReplayStore::new(limits()).unwrap();
    let now = Instant::now();
    let reference = EstablishmentReference::generate().unwrap();
    let deadline = now + Duration::from_secs(60);
    let association = store
        .establish(&context(), reference, b"binding", deadline, now)
        .unwrap();
    for support in [
        ReplaySupport::UnsupportedHost,
        ReplaySupport::HostPreparation,
    ] {
        let unsupported = TrustedContext {
            support,
            ..context()
        };
        assert_eq!(
            store.resolve(&unsupported, Some(association.handle), now),
            Err(ReplayError::UnsupportedCarrier)
        );
        assert_eq!(
            store.establish(&unsupported, reference, b"binding", deadline, now),
            Err(ReplayError::UnsupportedCarrier)
        );
    }
    for unavailable in [
        TrustedContext {
            principal: "",
            ..context()
        },
        TrustedContext {
            generation: "",
            ..context()
        },
        TrustedContext {
            deployment_id: "",
            ..context()
        },
        TrustedContext {
            scope: "",
            ..context()
        },
        TrustedContext {
            support: ReplaySupport::Unavailable,
            ..context()
        },
    ] {
        assert_eq!(
            store.resolve(&unavailable, Some(association.handle), now),
            Err(ReplayError::DependencyUnavailable)
        );
        assert_eq!(
            store.recover(&unavailable, reference, now),
            Err(ReplayError::DependencyUnavailable)
        );
        assert_eq!(
            store.resolve(&unavailable, None, now),
            Err(ReplayError::DependencyUnavailable)
        );
    }
    assert_eq!(store.recover(&context(), reference, now), Ok(association));
    let other = TrustedContext {
        conversation: Some("new"),
        ..context()
    };
    assert_eq!(
        store.establish(&other, reference, b"binding", now, now),
        Err(ReplayError::DependencyUnavailable)
    );
    assert_eq!(
        store.establish(&other, reference, b"", deadline, now),
        Err(ReplayError::DependencyUnavailable)
    );
    assert_eq!(store.resolve(&other, None, now), Ok(Resolution::CheckOnly));
    let without_conversation = TrustedContext {
        conversation: None,
        ..context()
    };
    assert_eq!(
        store.resolve(&without_conversation, None, now),
        Ok(Resolution::CheckOnly)
    );
    for conversation in [None, Some("unknown")] {
        let unavailable = TrustedContext {
            conversation,
            support: ReplaySupport::Unavailable,
            ..context()
        };
        assert_eq!(
            store.resolve(&unavailable, None, now),
            Err(ReplayError::DependencyUnavailable)
        );
    }
    assert_eq!(
        store.establish(&without_conversation, reference, b"binding", deadline, now),
        Err(ReplayError::InvalidCarrier)
    );
}

#[test]
fn invalid_limits_are_rejected_before_store_use() {
    for invalid in [
        ReplayLimits {
            max_record_bytes: 0,
            ..limits()
        },
        ReplayLimits {
            max_principal_records: 0,
            ..limits()
        },
        ReplayLimits {
            max_principal_bytes: 0,
            ..limits()
        },
        ReplayLimits {
            max_total_records: 0,
            ..limits()
        },
        ReplayLimits {
            max_total_bytes: 0,
            ..limits()
        },
        ReplayLimits {
            cleanup_batch_limit: 0,
            ..limits()
        },
        ReplayLimits {
            max_record_bytes: 1_638_401,
            ..limits()
        },
        ReplayLimits {
            max_principal_bytes: 16_384_001,
            ..limits()
        },
        ReplayLimits {
            max_principal_records: 1001,
            ..limits()
        },
    ] {
        assert!(matches!(
            ReplayStore::new(invalid),
            Err(ReplayError::InvalidConfiguration)
        ));
    }
}

#[test]
fn diagnostic_formatting_redacts_carriers_in_containing_types() {
    let reference = EstablishmentReference::generate().unwrap();
    let store = ReplayStore::new(limits()).unwrap();
    let now = Instant::now();
    let association = store
        .establish(
            &context(),
            reference,
            b"binding",
            now + Duration::from_secs(60),
            now,
        )
        .unwrap();
    let rendered = format!(
        "{} {:?} {} {:?} {:?} {:?} {:?}",
        reference,
        reference,
        association.handle,
        association.handle,
        ReplayCarrier::Establish(reference),
        ReplayCarrier::Continue(association.handle),
        Resolution::Association(association.clone())
    );
    assert!(!rendered.contains(&reference.to_header_value()));
    assert!(!rendered.contains(&association.handle.to_header_value()));
    assert!(rendered.contains("[REDACTED]"));
}
