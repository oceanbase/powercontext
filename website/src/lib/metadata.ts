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

import type { Metadata } from 'next';
import type { Language } from './i18n';
import { absoluteSiteUrl, siteUrl, withBasePath } from './urls';

export const siteDescriptions = {
  en: 'PowerContext keeps project decisions, evidence, and task progress available when people or agents resume work in a new session.',
  zh: 'PowerContext 保存项目决定、依据和任务进展，供人和 Agent 在新会话中接着工作。',
};

export function canonicalPath(path: string) {
  if (path === '/' || path.replace(/\/$/, '') === '/en') return '/';
  return `${path.replace(/\/$/, '')}/`;
}

type PageMetadataOptions = {
  path: string;
  lang?: Language;
  title?: string;
  description?: string;
  translations?: Partial<Record<Language, string>>;
  image?: { url: string; alt?: string };
  markdown?: string;
  article?: { publishedTime: string; modifiedTime: string; authors: string[] };
};

export function pageMetadata({
  path, lang = 'en', title, description = siteDescriptions[lang], translations, image, article, markdown,
}: PageMetadataOptions): Metadata {
  const url = absoluteSiteUrl(canonicalPath(path));
  const images = image
    ? [{ ...image, url: absoluteSiteUrl(image.url) }]
    : [{ url: absoluteSiteUrl('/og.png'), width: 1200, height: 630, alt: 'PowerContext' }];
  const alternateLanguages = translations && Object.fromEntries(
    Object.entries(translations).map(([locale, route]) => [locale, absoluteSiteUrl(canonicalPath(route))]),
  );
  if (alternateLanguages?.en) alternateLanguages['x-default'] = alternateLanguages.en;

  return {
    ...(title ? { title } : {}),
    description,
    alternates: {
      canonical: url,
      ...(alternateLanguages ? { languages: alternateLanguages } : {}),
      ...(markdown ? { types: { 'text/markdown': absoluteSiteUrl(markdown) } } : {}),
    },
    openGraph: {
      type: article ? 'article' : 'website',
      title: title ? `${title} · PowerContext` : 'PowerContext',
      description, url, siteName: 'PowerContext', images,
      locale: lang === 'zh' ? 'zh_CN' : 'en_US',
      alternateLocale: Object.keys(translations ?? {}).filter((locale) => locale !== lang)
        .map((locale) => locale === 'zh' ? 'zh_CN' : 'en_US'),
      ...article,
    },
    twitter: { card: 'summary_large_image', title: title ?? 'PowerContext', description, images },
  };
}

export const siteMetadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: 'PowerContext',
    template: '%s · PowerContext',
  },
  description: siteDescriptions.en,
  icons: { icon: withBasePath('/icon.svg') },
};
