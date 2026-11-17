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
import type { CallContext } from './contracts.ts'
import { LaunchService, launchRecords } from './launch.ts'
import { planNativeLaunch, type LaunchPlanner } from './native-launch.ts'
import { PreflightService, preflightRecords, type PreflightAuthority } from './preflight.ts'
import type { TargetCatalog } from './targets.ts'
import type { BoundaryReader } from './materials.ts'
import { LocalReceiptReader, type ReceiptConnectionResolver } from './receipt.ts'

interface HandoffServicesOptions {
  root: string
  authority: PreflightAuthority
  catalog: TargetCatalog
  client: Pick<PowerContextClient, 'request'>
  signal: AbortSignal
  authorization?: string
  plan?: LaunchPlanner
  readBoundary?: BoundaryReader
  resolveReceiptConnection?: ReceiptConnectionResolver
}

/** The only boundary that knows both implementations and their independent storage roots. */
export function createHandoffServices({ root, authority, catalog, client, signal, authorization, plan,
  readBoundary, resolveReceiptConnection }: HandoffServicesOptions) {
  let delivery: LocalDeliveryService
  const preflight = new PreflightService({
    authority, catalog, signal, records: preflightRecords(join(root, 'preflights')),
    referencedPreflightIds: () => delivery.referencedPreflightIds(),
  })
  // Receipt setup can fail on a damaged directory; do it before acquiring the delivery store lock.
  const receipt = resolveReceiptConnection ? new LocalReceiptReader({ directory: join(root, 'receipts'),
    resolveConnection: resolveReceiptConnection }) : undefined
  delivery = new LocalDeliveryService({
    directory: join(root, 'deliveries'),
    preflightGate: preflight,
    readBoundary,
    resolveConnection: async (context, binding) => {
      authority.requireLocal()
      const assertInstaller = preflight.preparationInstallGuard(context, binding)
      const live = await authority.sourceConnection(context, binding.source)
      assertInstaller()
      if (live.serverIdentity !== binding.serverIdentity || live.scopeId !== binding.scopeId
        || live.connectionRef !== binding.connectionRef || live.connectionGeneration !== binding.connectionGeneration) {
        throw new DeliveryError('binding_mismatch')
      }
      const capabilities = readBoundary ? await client.request('get_capabilities', {}, context.signal) : undefined
      assertInstaller()
      const value = capabilities?.kind === 'json' ? capabilities.value : undefined
      const enabled = value && typeof value === 'object' && !Array.isArray(value)
        && (value as Record<string, unknown>).handoff_generation === true
      return { client, assertDispatch: assertInstaller, ...(enabled ? { generation: { maxInputBytes: 192 * 1024, maxOutputBytes: 8000 } } : {}) }
    },
  })
  const launch = new LaunchService({
    preflight, delivery, requireLocal: () => authority.requireLocal(),
    plan: plan ?? ((target, cwd, intent) => planNativeLaunch(target, cwd, intent, authorization)),
    records: launchRecords(join(root, 'launch-attempts')), signal,
  })
  return {
    preflight, delivery, launch, receipt,
    async maintain(context: CallContext) {
      await delivery.prune(context, receipt ? bundle => receipt.confirmedAt(context, bundle) : undefined)
      if (receipt) await receipt.maintain(context, new Set((await delivery.list(context)).map(item => item.deliveryId)))
    },
    async close() {
      // Closing delivery stops any pending preparation. Hold the Host lease until
      // both stores have finished settling work and recording outcomes.
      const [, , shutdown] = await Promise.allSettled([preflight.drain(), launch.drain(), delivery.close()])
      if (shutdown.status === 'rejected') throw shutdown.reason
    },
  }
}
