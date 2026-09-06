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

import { readdir, readFile } from 'node:fs/promises';
import path from 'node:path';

const websiteDirectory = path.resolve('.');
const repositoryDirectory = path.resolve(websiteDirectory, '..');
const outputDirectory = path.join(websiteDirectory, 'out');
const docsDirectory = path.join(repositoryDirectory, 'docs');
const locales = ['en', 'zh'] as const;

async function collectHtmlFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map((entry) => {
      const entryPath = path.join(directory, entry.name);
      return entry.isDirectory() ? collectHtmlFiles(entryPath) : [entryPath];
    }),
  );

  return files.flat().filter((file) => file.endsWith('.html'));
}

function routeFromOutputFile(file: string) {
  const relativePath = path.relative(outputDirectory, file).split(path.sep).join('/');
  const routeDirectory = path.posix.dirname(relativePath);
  return routeDirectory === '.' ? '/' : `/${routeDirectory}`;
}

function normalizeRoute(pathname: string) {
  if (pathname === '/') return pathname;
  return pathname.replace(/\/$/, '');
}

function renderedMarkup(document: string) {
  return document.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
}

const rfcRoutes = new Set<string>();
for (const locale of locales) {
  const rfcDirectory = path.join(docsDirectory, locale, 'rfcs');
  const rfcFiles = (await readdir(rfcDirectory)).filter((file) => file.endsWith('.md'));

  for (const file of rfcFiles) {
    const slug = file === 'README.md' ? '' : `/${file.slice(0, -'.md'.length)}`;
    rfcRoutes.add(`/${locale}/rfcs${slug}`);
  }
}

const htmlFiles = await collectHtmlFiles(outputDirectory);
const exportedDocuments = new Map(
  await Promise.all(
    htmlFiles.map(async (file) => [routeFromOutputFile(file), await readFile(file, 'utf8')] as const),
  ),
);
const requiredRoutes = ['/', '/en', '/zh', '/en/docs', '/zh/docs', '/api', '/en/modules', '/zh/modules'];
const missingRequiredRoutes = requiredRoutes.filter((route) => !exportedDocuments.has(route));
const missingRfcRoutes = [...rfcRoutes].filter((route) => !exportedDocuments.has(route));

if (missingRequiredRoutes.length > 0 || missingRfcRoutes.length > 0) {
  const missingRoutes = [...missingRequiredRoutes, ...missingRfcRoutes].sort();
  throw new Error(`Public pages are missing from the static site:\n${missingRoutes.join('\n')}`);
}

const brokenLinks = new Set<string>();
for (const [route, document] of exportedDocuments) {
  const pageUrl = new URL(route === '/' ? '/' : `${route}/`, 'https://powercontext.oceanbase.io');
  const markup = renderedMarkup(document);

  for (const match of markup.matchAll(/<a\b[^>]*\bhref="([^"]+)"/g)) {
    const href = match[1].replaceAll('&amp;', '&');
    const target = new URL(href, pageUrl);
    if (target.origin !== pageUrl.origin) continue;

    const fileName = path.posix.basename(target.pathname);
    if (fileName.includes('.')) continue;

    const targetRoute = normalizeRoute(decodeURIComponent(target.pathname));
    if (!exportedDocuments.has(targetRoute)) brokenLinks.add(`${route} -> ${targetRoute}`);
  }
}

if (brokenLinks.size > 0) {
  throw new Error(`Public pages contain broken internal links:\n${[...brokenLinks].sort().join('\n')}`);
}

const rootDocument = exportedDocuments.get('/')!;
const englishDocument = exportedDocuments.get('/en')!;
const chineseDocument = exportedDocuments.get('/zh')!;
const docsDocuments = {
  en: exportedDocuments.get('/en/docs')!,
  zh: exportedDocuments.get('/zh/docs')!,
};
const siteUrl = 'https://powercontext.oceanbase.io';
const homeAlternates = [
  `<link rel="alternate" hrefLang="en" href="${siteUrl}/"`,
  `<link rel="alternate" hrefLang="zh" href="${siteUrl}/zh/"`,
  `<link rel="alternate" hrefLang="x-default" href="${siteUrl}/"`,
];

if (/http-equiv="refresh"/i.test(rootDocument) || !rootDocument.includes('Keep work moving')) {
  throw new Error('Static root page does not render the default English site.');
}

if (
  !rootDocument.includes('<html lang="en"')
  || !englishDocument.includes('<html lang="en"')
  || !chineseDocument.includes('<html lang="zh"')
) {
  throw new Error('Static localized pages do not declare the expected document language.');
}

if (
  !rootDocument.includes(`<link rel="canonical" href="${siteUrl}/"`)
  || !englishDocument.includes(`<link rel="canonical" href="${siteUrl}/"`)
  || !chineseDocument.includes(`<link rel="canonical" href="${siteUrl}/zh/"`)
  || homeAlternates.some(
    (alternate) =>
      !rootDocument.includes(alternate)
      || !englishDocument.includes(alternate)
      || !chineseDocument.includes(alternate),
  )
) {
  throw new Error('Static home pages do not declare the expected canonical and language alternate URLs.');
}

if (rootDocument.includes('href="/en/"')) {
  throw new Error('Static root page links to the duplicate English home URL.');
}

for (const [locale, document] of Object.entries(docsDocuments)) {
  const markup = renderedMarkup(document);
  const apiReferenceLabel = locale === 'en' ? 'API Reference' : 'API 参考';
  const apiReferenceIndex = markup.indexOf(`>${apiReferenceLabel}</p>`);
  const referenceIndex = markup.indexOf('>Reference<');
  const pythonApiIndex = markup.indexOf('>Python API</a>');
  const rfcIndex = markup.indexOf('>RFCs<');

  if (
    rfcIndex === -1
    || referenceIndex < rfcIndex
    || apiReferenceIndex < referenceIndex
    || pythonApiIndex < apiReferenceIndex
  ) {
    throw new Error(`Static ${locale} documentation does not show RFCs before Reference.`);
  }
}

const exportedRoutes = [...exportedDocuments.keys()];
const httpApiPageCount = exportedRoutes.filter((route) => route === '/api' || route.startsWith('/api/')).length;
const pythonApiPageCount = exportedRoutes.filter((route) => /^\/(en|zh)\/modules(?:\/|$)/.test(route)).length;
const rfcPageCount = exportedRoutes.filter((route) => /^\/(en|zh)\/rfcs(?:\/|$)/.test(route)).length;

console.log(
  `Verified ${exportedDocuments.size} public pages and their internal links `
  + `(${httpApiPageCount} HTTP API, ${pythonApiPageCount} Python API, ${rfcPageCount} RFC).`,
);
