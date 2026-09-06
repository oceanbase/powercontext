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

import { defineI18n } from 'fumadocs-core/i18n';
import { defineI18nUI } from 'fumadocs-ui/i18n';

export const languages = ['en', 'zh'] as const;
export type Language = (typeof languages)[number];
export const defaultLanguage: Language = 'en';

export const i18n = defineI18n({
  defaultLanguage,
  languages: [...languages],
  parser: 'dir',
  hideLocale: 'never',
});

export const i18nUI = defineI18nUI(i18n, {
  en: { displayName: 'English' },
  zh: { displayName: '简体中文' },
});

export function isLanguage(value: string): value is Language {
  return languages.includes(value as Language);
}
