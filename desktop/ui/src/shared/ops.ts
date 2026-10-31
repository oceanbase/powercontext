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

import { invoke } from "@tauri-apps/api/core";
import { getCurrentWebviewWindow } from "@tauri-apps/api/webviewWindow";
import type {
  DesktopState,
  OpsApprovalInput,
  OpsCancelInput,
  OpsInspectionReply,
  OpsOperationReply,
  OpsPreflightInput,
  OpsPreflightReply,
  OpsProgress,
  OpsReferences,
  OpsStatusInput,
  OpsValidationReply,
} from "../generated/ipc";

/** Subscribe before dispatch; operation references survive a missed event or reply. */
export const subscribeOpsProgress = (receive: (event: OpsProgress) => void) =>
  getCurrentWebviewWindow().listen<OpsProgress>("ops-progress", (event) =>
    receive(event.payload),
  );

/** Fixed Ops v1 operations and explicit setup handoff; executable, binding and complete plans remain native. */
export const opsApi = {
  connect: (generation: number) =>
    invoke<DesktopState>("connect_local_server", { generation }),
  references: () => invoke<OpsReferences>("ops_references"),
  inspect: () => invoke<OpsInspectionReply>("ops_inspect"),
  configValidate: () => invoke<OpsValidationReply>("ops_config_validate"),
  preflight: (input: OpsPreflightInput) =>
    invoke<OpsPreflightReply>("ops_preflight", { input }),
  apply: (input: OpsApprovalInput) =>
    invoke<OpsOperationReply>("ops_apply", { input }),
  operationStatus: (input: OpsStatusInput) =>
    invoke<OpsOperationReply>("ops_operation_status", { input }),
  cancel: (input: OpsCancelInput) =>
    invoke<OpsOperationReply>("ops_cancel", { input }),
  resume: (input: OpsApprovalInput) =>
    invoke<OpsOperationReply>("ops_resume", { input }),
};
