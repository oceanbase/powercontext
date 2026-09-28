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

import assert from 'node:assert/strict';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { scanURLs, validateFiles } from 'next-validate-link';

for (const appDirectory of ['app', 'src/app']) {
  test(`Next routes and link validation work with ${appDirectory}`, async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), 'pc-links-'));
    try {
      for (const route of [
        'page.tsx',
        '(default)/api/page.tsx',
        '(localized)/[lang]/(documentation)/docs/[[...slug]]/page.tsx',
      ]) {
        const file = path.join(root, appDirectory, route);
        await mkdir(path.dirname(file), { recursive: true });
        await writeFile(file, 'export default function Page() { return null; }\n');
      }
      const scanned = await scanURLs({
        preset: 'next',
        cwd: root,
        populate: {
          '(localized)/[lang]/(documentation)/docs/[[...slug]]': ['en', 'zh'].flatMap((lang) => [
            { value: { lang, slug: [] }, hashes: [] },
            { value: { lang, slug: ['guide', 'start'] }, hashes: ['intro'] },
          ]),
        },
      });
      assert.deepEqual([...scanned.urls.keys()].sort(), [
        '/', '/api', '/en/docs', '/en/docs/guide/start', '/zh/docs', '/zh/docs/guide/start',
      ]);
      const file = path.join(root, 'links.md');
      await writeFile(file, [
        '[API](/api)',
        '[English index](/en/docs)',
        '[English guide](/en/docs/guide/start#intro)',
        '[Chinese index](/zh/docs)',
        '[Chinese guide](/zh/docs/guide/start#intro)',
      ].join('\n'));
      assert.deepEqual(await validateFiles([file], { scanned }), []);

      await writeFile(file, '[Missing page](/missing)\n[Missing heading](/en/docs/guide/start#missing)\n');
      const errors = await validateFiles([file], { scanned });
      assert.deepEqual(errors.flatMap((result) => result.errors.map((error) => error.url)).sort(), [
        '/en/docs/guide/start#missing', '/missing',
      ]);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}
