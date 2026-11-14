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
import { homedir } from 'node:os'
import { isAbsolute, relative } from 'node:path'
import type { LaunchBundle } from './contracts.ts'
import { DeliveryError } from './contracts.ts'
import type { HandoffLocale } from './locale.ts'

/** The receiver's PowerContext plugin verifies the package and supplies its protocol instructions. */
export function buildReceiverPrompt(bundle: LaunchBundle, locale: HandoffLocale = 'en'): string {
  const fromHome = relative(homedir(), bundle.packagePath)
  const path = fromHome.startsWith('.powercontext-dsh-handoff/') && isAbsolute(bundle.packagePath)
    ? '~/' + fromHome : bundle.packagePath
  const prompt = (locale === 'zh-CN'
    ? '请用 PowerContext 读取这份交接包，继续剩下的工作，并用简体中文回复：'
    : 'Please use PowerContext to read this handoff package and continue the remaining work. Reply in English:'
  ) + '\n' + JSON.stringify(path)
  if (Buffer.byteLength(prompt, 'utf8') > 2048) throw new DeliveryError('payload_invalid')
  return prompt
}
