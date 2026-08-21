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

import { Context } from "@deepseek-ai/cordis";

//#region src/config.d.ts
interface PluginConfig {
  baseUrl?: string;
  authorization?: string;
  scopeId?: string;
  timeoutMs?: number;
  requestTimeoutMs?: number;
  maxBytes?: number;
  capturePrompts?: boolean;
  flushOnCapture?: boolean;
  flushMaxCalls?: number;
}
interface ResolvedConfig {
  baseUrl: string;
  authorization: string | undefined;
  scopeId: string | undefined;
  timeoutMs: number;
  requestTimeoutMs: number;
  maxBytes: number;
  capturePrompts: boolean;
  flushOnCapture: boolean;
  flushMaxCalls: number;
}
//#endregion
//#region src/index.d.ts
declare const name = "powercontext-dsh";
declare const inject: string[];
interface Config extends PluginConfig {}
declare const Config: {
  '~standard': {
    version: 1;
    vendor: string;
    validate(value: unknown): {
      value: ResolvedConfig;
      issues?: undefined;
    } | {
      issues: {
        message: string;
      }[];
      value?: undefined;
    };
  };
};
declare function apply(ctx: Context, config: Config): Promise<void>;
//#endregion
export { Config, apply, inject, name };
