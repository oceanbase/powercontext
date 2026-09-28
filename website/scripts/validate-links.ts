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

import { execFile } from 'node:child_process';
import { readdir } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';
import { register } from 'fumadocs-mdx/node';
import {
  printErrors,
  scanURLs,
  validateFiles,
  type FileObject,
  type ValidateConfig,
} from 'next-validate-link';

const execFileAsync = promisify(execFile);
const websiteDirectory = path.resolve('.');
const repositoryDirectory = path.resolve(websiteDirectory, '..');
const missingSourcePage = '/__missing_source_page__';

register();

const [{ source }, { languages }] = await Promise.all([
  import('../src/lib/source'),
  import('../src/lib/i18n'),
]);

const sourcePages = source.getPages();
const documentationPages = sourcePages.filter((page) => page.slugs[0] === 'docs');
const rfcPages = sourcePages.filter((page) => page.slugs[0] === 'rfcs');
const developmentPages = sourcePages.filter((page) => page.slugs[0] === 'development');
const scanned = await scanURLs({
  preset: 'next',
  populate: {
    '(localized)/[lang]': languages.map((lang) => ({ value: { lang } })),
    '(localized)/[lang]/(documentation)/docs/[[...slug]]': documentationPages.map((page) => ({
      value: { lang: page.locale ?? 'en', slug: page.slugs.slice(1) },
      hashes: page.data.toc.map((item) => item.url.slice(1)),
    })),
    '(localized)/[lang]/(documentation)/rfcs/[[...slug]]': rfcPages.map((page) => ({
      value: { lang: page.locale ?? 'en', slug: page.slugs.slice(1) },
      hashes: page.data.toc.map((item) => item.url.slice(1)),
    })),
    '(localized)/[lang]/(documentation)/development/[[...slug]]': developmentPages.map((page) => ({
      value: { lang: page.locale ?? 'en', slug: page.slugs.slice(1) },
      hashes: page.data.toc.map((item) => item.url.slice(1)),
    })),
  },
});

// Public assets have URLs even though they are not application routes.
const publicDirectory = path.join(websiteDirectory, 'public');
for (const entry of await readdir(publicDirectory, { recursive: true, withFileTypes: true })) {
  if (!entry.isFile()) continue;
  const relativePath = path.relative(publicDirectory, path.join(entry.parentPath, entry.name));
  scanned.urls.set('/' + relativePath.split(path.sep).join('/'), {});
}

for (const [url, metadata] of scanned.urls) {
  if (url !== '/' && !url.endsWith('/')) scanned.urls.set(`${url}/`, metadata);
}

const sourceFiles: FileObject[] = await Promise.all(
  sourcePages.map(async (page) => {
    if (!page.absolutePath) throw new Error(`Missing source path for ${page.url}`);
    return {
      path: page.absolutePath,
      content: await page.data.getText('raw'),
      data: page.data,
      url: page.url,
    };
  }),
);
const sourceUrls = new Map(sourceFiles.map((file) => [path.normalize(file.path), file.url!]));
const sourceErrors = await validateFiles(sourceFiles, {
  scanned,
  checkRelativePaths: 'as-url',
  determinatePathname: (pathname) => {
    if (/\.mdx?$/.test(pathname)) return 'relative-file-path';
    return pathname.startsWith('.') ? 'relative-url' : 'url';
  },
  pathToUrl: (file) => sourceUrls.get(path.normalize(file)) ?? missingSourcePage,
  markdown: {
    components: {
      Card: { attributes: ['href'] },
      DynamicLink: { attributes: ['href'] },
    },
  },
});

const { stdout } = await execFileAsync('git', ['ls-files', '-z', '--', '*.md', '*.mdx'], {
  cwd: repositoryDirectory,
  encoding: 'utf8',
});
const repositoryFiles = stdout
  .split('\0')
  .filter(Boolean)
  .map((file) => path.join(repositoryDirectory, file));
const repositoryMarkdown = {
  onNode(node) {
    if (node.type === 'link' || node.type === 'image') return { hrefs: [node.url] };
    if (node.type !== 'html') return { hrefs: [] };

    const hrefs = [...node.value.matchAll(/\b(?:href|src)=(['"])(.*?)\1/gi)].map((match) => match[2]);
    return { hrefs };
  },
} satisfies NonNullable<ValidateConfig['markdown']>;
const repositoryErrors = await validateFiles(repositoryFiles, {
  scanned,
  checkRelativePaths: 'exists',
  checkRelativeUrls: false,
  determinatePathname: (pathname) => pathname.startsWith('/') ? 'url' : 'relative-file-path',
  markdown: repositoryMarkdown,
});

const errors = [...sourceErrors, ...repositoryErrors];
if (errors.length > 0) printErrors(errors, true);

console.log(`Validated links in ${sourceFiles.length} public pages and ${repositoryFiles.length} repository files.`);
