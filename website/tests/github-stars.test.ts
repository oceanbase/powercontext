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
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { GitHubStars } from '../src/components/github-stars';

const url = 'https://github.com/oceanbase/powercontext';

test('missing build data leaves a usable link without a placeholder or invented count', () => {
  for (const lang of ['zh', 'en'] as const) {
    const html = renderToStaticMarkup(createElement(GitHubStars, { lang, url }));
    const linkText = html.match(/<a\b[^>]*>([\s\S]*?)<\/a>/)?.[1].replace(/<[^>]*>/g, '');
    assert.ok(linkText !== undefined, 'the repository link must be present');
    assert.doesNotMatch(linkText, /\bStar\b|\d/, 'do not show a loading label or invented count');
    assert.ok(html.includes(`href="${url}"`), 'the repository link remains usable before data arrives');
    assert.ok(html.includes('aria-label="GitHub · '));
  }
});

test('first paint includes the build count in both languages, including zero', () => {
  for (const lang of ['zh', 'en'] as const) {
    for (const initialCount of [985, 0]) {
      const html = renderToStaticMarkup(createElement(GitHubStars, { lang, url, initialCount }));
      const linkText = html.match(/<a\b[^>]*>([\s\S]*?)<\/a>/)?.[1].replace(/<[^>]*>/g, '');
      assert.ok(linkText?.includes(String(initialCount)), 'the count must be present before hydration');
      assert.ok(html.includes(`GitHub · ${initialCount} stars`));
    }
  }
});
