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
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { getBenchmarkContent } from '../src/lib/benchmark-content';
import { getHomeContent } from '../src/lib/home-content';

const repositoryDir = fileURLToPath(new URL('../../', import.meta.url));

for (const { name, relativePath, load, missingContent } of [
  { name: 'Home', relativePath: 'index.md', load: getHomeContent, missingContent: 'Home content is missing' },
  {
    name: 'Benchmark', relativePath: 'benchmarks/index.md', load: getBenchmarkContent,
    missingContent: 'Benchmark data is missing',
  },
]) {
  for (const lang of ['en', 'zh'] as const) {
    test(`${name} (${lang}) accepts LF, CRLF, and mixed line endings`, async () => {
      const source = await readFile(path.join(repositoryDir, 'docs', lang, relativePath), 'utf8');
      const lf = source.replace(/\r\n/g, '\n');
      const root = await mkdtemp(path.join(os.tmpdir(), 'pc-content-'));
      const cwd = process.cwd();
      try {
        await mkdir(path.join(root, 'website'));
        const target = path.join(root, 'docs', lang, relativePath);
        await mkdir(path.dirname(target), { recursive: true });
        process.chdir(path.join(root, 'website'));
        await writeFile(target, lf);
        const expected = await load(lang);
        assert.ok(Object.keys(expected).length > 0);
        for (const content of [
          lf.replace(/\n/g, '\r\n'),
          lf.replace(/^---\n/, '---\r\n'),
          lf.replace(/\n/g, '\r\n').replace(/^---\r\n/, '---\n'),
        ]) {
          await writeFile(target, content);
          assert.deepEqual(await load(lang), expected);
        }
      } finally {
        process.chdir(cwd);
        await rm(root, { recursive: true, force: true });
      }
    });
  }

  test(`${name} retains errors for missing or invalid content`, async () => {
    const root = await mkdtemp(path.join(os.tmpdir(), 'pc-content-'));
    const cwd = process.cwd();
    try {
      await mkdir(path.join(root, 'website'));
      const target = path.join(root, 'docs', 'en', relativePath);
      await mkdir(path.dirname(target), { recursive: true });
      process.chdir(path.join(root, 'website'));
      for (const newline of ['\n', '\r\n']) {
        for (const [source, error] of [
          ['# No frontmatter\n', new RegExp(`${name} frontmatter is missing`)],
          ['---\nother: {}\n', new RegExp(`${name} frontmatter is missing`)],
          ['---\nother: {}\n---\n', new RegExp(missingContent)],
          ['---\ninvalid: [\n---\n', { name: 'YAMLParseError' }],
        ] as const) {
          await writeFile(target, source.replace(/\n/g, newline));
          await assert.rejects(() => load('en'), error);
        }
      }
    } finally {
      process.chdir(cwd);
      await rm(root, { recursive: true, force: true });
    }
  });
}
