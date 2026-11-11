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

import { afterEach, describe, expect, it, vi } from 'vitest'
import { LOCALE_KEY, readLocale, saveLocale, texts } from '../src/ui/locale.ts'

afterEach(() => vi.unstubAllGlobals())
describe('diagnostic language preference', () => {
  it('defaults to Chinese, persists English and rejects invalid saved values', () => {
    const values = new Map<string, string>()
    vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value) })
    expect(readLocale()).toBe('zh-CN')
    saveLocale('en')
    expect(readLocale()).toBe('en')
    values.set(LOCALE_KEY, 'fr')
    expect(readLocale()).toBe('zh-CN')
    saveLocale('zh-CN')
    expect(readLocale()).toBe('zh-CN')
    expect(Object.keys(texts.en).sort()).toEqual(Object.keys(texts['zh-CN']).sort())
  })
  it('still renders with Chinese fallback when local storage is blocked', () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('blocked') } })
    expect(readLocale()).toBe('zh-CN')
    expect(() => saveLocale('en')).not.toThrow()
  })
})
