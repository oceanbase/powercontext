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


import { join } from 'node:path'
import type { PowerContextClient } from '../client.ts'
import { LocalDeliveryService } from './delivery.ts'
import { DeliveryError } from './contracts.ts'
import { LaunchService, launchRecords } from './launch.ts'
import { planNativeLaunch, type LaunchPlanner } from './native-launch.ts'
import { PreflightService, preflightRecords, type PreflightAuthority } from './preflight.ts'
import type { TargetCatalog } from './targets.ts'

interface HandoffServicesOptions {
  root: string
  authority: PreflightAuthority
  catalog: TargetCatalog
  client: Pick<PowerContextClient, 'request'>
  signal: AbortSignal
  authorization?: string
  plan?: LaunchPlanner
}

/** The only boundary that knows both implementations and their independent storage roots. */
export function createHandoffServices({
  root, authority, catalog, client, signal, authorization, plan,
}: HandoffServicesOptions) {
  let delivery: LocalDeliveryService
  const preflight = new PreflightService({
    authority, catalog, signal, records: preflightRecords(join(root, 'preflights')),
    referencedPreflightIds: () => delivery.referencedPreflightIds(),
  })
  delivery = new LocalDeliveryService({
    directory: join(root, 'deliveries'),
    preflightGate: preflight,
    resolveConnection: async (context, binding) => {
      authority.requireLocal()
      const assertInstaller = preflight.preparationInstallGuard(context, binding)
      const live = await authority.sourceConnection(context, binding.source)
      assertInstaller()
      if (live.serverIdentity !== binding.serverIdentity || live.scopeId !== binding.scopeId
        || live.connectionRef !== binding.connectionRef || live.connectionGeneration !== binding.connectionGeneration) {
        throw new DeliveryError('binding_mismatch')
      }
      // The configured Host client keeps authorization in memory. This composition
      // supports inspected work input and exact references, without a session content reader.
      return { client, assertDispatch: assertInstaller }
    },
  })
  const launch = new LaunchService({
    preflight, delivery, requireLocal: () => authority.requireLocal(),
    plan: plan ?? ((target, cwd, intent) => planNativeLaunch(target, cwd, intent, authorization)),
    records: launchRecords(join(root, 'launch-attempts')), signal,
  })
  return {
    preflight, delivery, launch,
    async close() {
      // Closing delivery stops any pending preparation. Hold the Host lease until
      // both stores have finished settling work and recording outcomes.
      const [, , shutdown] = await Promise.allSettled([preflight.drain(), launch.drain(), delivery.close()])
      if (shutdown.status === 'rejected') throw shutdown.reason
    },
  }
}
