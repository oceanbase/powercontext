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

declare module '@deepseek-ai/cordis' {
  export interface Context {
    tools: { register(tool: unknown): () => void }
    on(event: string, handler: (...args: never[]) => unknown): () => void
    get(name: string): unknown
    logger: { warn(message: string): void; debug?(message: string): void }
  }
}

declare module '@deepseek-ai/schemastery' {
  type Schema<T = unknown> = {
    (value: unknown): T
  }
  interface Builder {
    object(shape: Record<string, unknown>): Schema
    string(): { default(value: string): unknown; required(): unknown }
    number(): { default(value: number): unknown; required(): unknown }
    boolean(): { default(value: boolean): unknown; required(): unknown }
  }
  const z: Builder
  export default z
  export type { Schema }
}

declare module '@deepseek-ai/dsh-tools' {
  export function defineTool(definition: Record<string, unknown>): unknown
}

declare module '@deepseek-ai/dsh-agent' {
  export type PreStepDecision =
    | { kind: 'reject' }
    | { kind: 'enter'; messages: unknown[] }
  export type Agent = {
    session: { header: { id: string; cwd?: string } }
  }
}

declare module '@deepseek-ai/dsh-llm' {
  export function createUserMessage(input: {
    content: Array<{ type: 'text'; text: string }>
    source: { kind: 'plugin'; plugin: string }
  }): unknown
}

declare module '@deepseek-ai/dsh-session' {
  export type UserMessage = {
    readonly content: ReadonlyArray<{ readonly type: string; readonly text?: string }>
    readonly source: {
      readonly kind: string
      readonly [key: string]: unknown
    }
  }
}

declare module '@deepseek-ai/dsh-system-prompt' {}
declare module '@deepseek-ai/dsh-commands' {}
declare module '@deepseek-ai/dsh-skill' {}
