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

import type { OperationSummary, SessionSummary } from "../src/generated/ipc";

export function emptySessionSummary(): SessionSummary {
  return {
    schemaVersion: 2,
    sessionStartedAtMs: Date.UTC(2026, 9, 25, 0),
    windowStartedAtMs: Date.UTC(2026, 9, 25, 1),
    generatedAtMs: Date.UTC(2026, 9, 25, 2),
    capacity: 512,
    evicted: 0,
    droppedSinceStart: 0,
    coverage: {
      desktopSessionOnly: true,
      persistenceEvidence: false,
      httpOperations: [
        "get_access_principal",
        "remember_memory",
        "search_memory",
      ],
      userOperations: ["remember_memory", "search_memory"],
      identityChecksIncluded: true,
      installerOps: true,
    },
    operations: [],
    recent: [],
    installerOps: {
      capacity: 64,
      evicted: 0,
      droppedSinceStart: 0,
      recent: [],
    },
  };
}

export function recordedSessionSummary(): SessionSummary {
  const operations: OperationSummary[] = [
    {
      unit: "http_request",
      operation: "remember_memory",
      sampleCount: 4,
      outcomes: { success: 2, failure: 1, cancelled: 1, unknown: 0 },
      minDurationMs: 10,
      meanDurationMs: 25,
      maxDurationMs: 40,
    },
    {
      unit: "user_operation",
      operation: "remember_memory",
      sampleCount: 4,
      outcomes: { success: 1, failure: 1, cancelled: 0, unknown: 2 },
      minDurationMs: 20,
      meanDurationMs: 35,
      maxDurationMs: 50,
    },
  ];
  return {
    ...emptySessionSummary(),
    evicted: 3,
    droppedSinceStart: 2,
    operations,
    recent: [
      ...(["success", "success", "failure", "cancelled"] as const).map(
        (outcome) => ({
          unit: "http_request" as const,
          operation: "remember_memory" as const,
          outcome,
          completedAtMs: Date.UTC(2026, 9, 25, 2),
          durationMs: 25,
          httpStatus: null,
          errorCode: null,
        }),
      ),
      ...(["success", "failure", "unknown", "unknown"] as const).map(
        (outcome) => ({
          unit: "user_operation" as const,
          operation: "remember_memory" as const,
          outcome,
          completedAtMs: Date.UTC(2026, 9, 25, 2),
          durationMs: 35,
          httpStatus: null,
          errorCode: null,
        }),
      ),
    ],
  };
}
