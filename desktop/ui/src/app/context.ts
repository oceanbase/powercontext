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

import type { CheckReport, DesktopState } from "../generated/ipc";
import type { operations } from "../generated/operations";

export function operationSupported(
  report: CheckReport | undefined,
  operation: keyof typeof operations,
): boolean {
  return (
    !!report?.compatibilityVerified &&
    report.supportedOperations.includes(operation)
  );
}

// Presentation only: operation authorization remains at the native boundary.
export function activeCheckReport(state: DesktopState | null) {
  const active = state?.active;
  if (!active) return undefined;
  const profile = state?.profiles.find(
    (item) => item.id === active.connectionId,
  );
  if (profile && profile.revision !== active.report.revision) return undefined;
  const latest = state?.reports.find(
    (item) =>
      item.connectionId === active.connectionId &&
      item.revision === active.report.revision,
  );
  return latest && latest.checkedAt >= active.report.checkedAt
    ? latest
    : active.report;
}
