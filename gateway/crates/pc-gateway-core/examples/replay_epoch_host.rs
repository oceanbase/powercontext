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

//! Host protocol simulation through the public module; no HTTP, preparation, or model calls.

use std::time::{Duration, Instant};

use pc_gateway_core::replay_epoch::{
    EstablishmentReference, ReplayCarrier, ReplayHandle, ReplayLimits, ReplayStore, ReplaySupport,
    Resolution, TrustedContext,
};

struct HostAttempt {
    reference: EstablishmentReference,
    establishment_attempted: bool,
    handle: Option<ReplayHandle>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let store = ReplayStore::new(ReplayLimits {
        max_record_bytes: 8192,
        max_principal_records: 16,
        max_principal_bytes: 131_072,
        max_total_records: 64,
        max_total_bytes: 524_288,
        cleanup_batch_limit: 8,
    })?;
    let context = TrustedContext {
        deployment_id: "example-deployment",
        generation: "example-process-generation",
        principal: "authenticated-host-owner",
        scope: "example-scope",
        conversation: Some("powercontext:normal-response"),
        support: ReplaySupport::ReliableGateway,
    };
    let now = Instant::now();
    let deadline = now + Duration::from_secs(300);
    let compatibility = b"approved-target/configuration-v1/renderer-v1";

    let mut host = HostAttempt {
        reference: EstablishmentReference::generate()?,
        establishment_attempted: true,
        handle: None,
    };
    assert!(host.establishment_attempted);
    let reference_header = host.reference.to_header_value();
    assert_eq!(
        ReplayCarrier::parse(&["new"], &[&reference_header], context.conversation)?,
        ReplayCarrier::Establish(host.reference)
    );
    let response = store.establish(&context, host.reference, compatibility, deadline, now)?;
    let response_header = response.handle.to_header_value();
    host.handle = Some(ReplayHandle::parse(&response_header)?);
    let continuation_header = host
        .handle
        .ok_or("host must retain correlation")?
        .to_header_value();
    let continuation = ReplayCarrier::parse(&[&continuation_header], &[], context.conversation)?;
    let ReplayCarrier::Continue(handle) = continuation else {
        return Err("expected a continuation handle".into());
    };
    assert_eq!(
        store.resolve(&context, Some(handle), now)?,
        Resolution::Association(response)
    );
    println!("Normal and tool continuations return the same correlation handle.");

    let lost_context = TrustedContext {
        conversation: Some("powercontext:lost-response"),
        ..context
    };
    let mut lost_host = HostAttempt {
        reference: EstablishmentReference::generate()?,
        establishment_attempted: true,
        handle: None,
    };
    assert!(lost_host.establishment_attempted);
    drop(store.establish(
        &lost_context,
        lost_host.reference,
        compatibility,
        deadline,
        now,
    )?);
    assert!(lost_host.handle.is_none());
    let recovered = store.recover(&lost_context, lost_host.reference, now)?;
    lost_host.handle = Some(recovered.handle);
    assert!(lost_host.handle.is_some());
    assert_eq!(recovered.expires_at, deadline);
    assert_eq!(recovered.compatibility.as_ref(), compatibility);
    println!("Lost response: restored correlation using the saved attempt reference.");
    println!("Stop here: recovery restores no answer or execution facts and grants no regeneration permission.");
    Ok(())
}
