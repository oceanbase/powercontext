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
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { BenchmarkLeaderboards } from '../src/components/benchmark-leaderboards';
import { getBenchmarkContent } from '../src/lib/benchmark-content';
import { getHomeContent } from '../src/lib/home-content';

const repositoryDir = fileURLToPath(new URL('../../', import.meta.url));

test('LoCoMo Plus preserves the supplied model comparisons in both languages', async () => {
  const contents = await Promise.all((['en', 'zh'] as const).map((lang) => getBenchmarkContent(lang)));
  const expected = [
    { model: 'GPT-4o-mini', baseline: 40.855, jev: 49.78 },
    { model: 'Qwen3.7-plus', baseline: 60.37, jev: 68.947 },
    { model: 'GPT-4o', baseline: 65.082, jev: 69.28 },
  ];
  for (const content of contents) {
    const plus = content.locomo_plus;
    assert.deepEqual(plus.rows, expected);
    assert.equal(plus.embedding_model, 'qwen3.7-text-embedding');
    assert.match(plus.embedding_dimensions, /1024/);
    assert.deepEqual(plus.rows.map((row) => (row.jev - row.baseline).toFixed(3)), ['8.925', '8.577', '4.198']);
    const targets = content.hero.actions.map((action) => action.target);
    assert.equal(targets[targets.indexOf('locomo') + 1], 'locomo-plus');
    assert.ok(content.sources.groups.some((group) => group.id === 'locomo-plus'));
    // These results do not inherit the previous experiment's unconfirmed protocol.
    assert.doesNotMatch(JSON.stringify(plus), /2,387|55\.611|v7|Top-8/);
  }
});

test('LoCoMo Plus public comparisons retain Cognitive scores and linked evaluation details', async () => {
  const expected = [
    ['T-Mem', '74.81%'],
    ['HyperMem', '48.63%'], ['MemOS', '32.67%'], ['Gemini-2.5-Pro', '26.06%'],
    ['GPT-4o', '21.05%'], ['A-Mem', '17.20%'], ['Mem0', '15.80%'], ['SeCom', '14.90%'],
  ];
  for (const lang of ['en', 'zh'] as const) {
    const benchmark = await getBenchmarkContent(lang);
    const board = benchmark.leaderboards.locomo_plus;
    // Use the Cognitive score, not the adjacent performance-drop column in the papers.
    assert.deepEqual(board.rows.map((row) => [row.name, row.score]), expected);
    for (const row of board.rows) {
      assert.match(row.protocol, /Cognitive/);
      assert.ok(row.evidence.length > 0);
      assert.equal(new URL(row.source).protocol, 'https:');
    }
    assert.ok(benchmark.locomo_plus.rows.some((row) => row.model === board.spotlight.model));
    const markup = renderToStaticMarkup(createElement(BenchmarkLeaderboards, { benchmark, lang }));
    const tabIds = [...markup.matchAll(/id="([^"]+-results-tab)"/g)].map((match) => match[1]);
    assert.deepEqual(tabIds, ['locomo-results-tab', 'locomo-plus-results-tab', 'swe-results-tab']);
    assert.match(markup, /aria-controls="locomo-plus-results-panel"/);
    assert.match(markup, /aria-labelledby="locomo-plus-results-tab" id="locomo-plus-results-panel" role="tabpanel"/);
    const plusPanel = markup.split('id="locomo-plus-results-panel"')[1].split('id="swe-results-panel"')[0];
    for (const row of board.rows) {
      assert.ok(plusPanel.includes(row.name));
      assert.ok(plusPanel.includes(row.score));
      assert.ok(plusPanel.includes(`href="${row.source}"`));
    }
    assert.ok(plusPanel.includes('65.082%'));
    assert.ok(plusPanel.includes('69.28%'));
    assert.ok(plusPanel.includes('href="#locomo-plus"'));
  }
});

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
