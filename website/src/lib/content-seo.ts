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

import { isLanguage, languages } from './i18n';
import { pageMetadata } from './metadata';
import { getPageMarkdownUrl } from './markdown';
import { source } from './source';

type ContentPage = ReturnType<typeof source.getPages>[number];

export function contentMetadata(page: ContentPage) {
  const translations = Object.fromEntries(languages.flatMap((lang) => {
    const translated = source.getPage(page.slugs, lang);
    return translated ? [[lang, translated.url]] : [];
  }));
  return pageMetadata({
    path: page.url,
    lang: page.locale && isLanguage(page.locale) ? page.locale : 'en',
    title: page.data.title,
    description: page.data.description,
    translations,
    markdown: getPageMarkdownUrl(page).url,
  });
}
