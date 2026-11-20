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
import test from 'node:test';
import { rehypeStaticHtml } from '../src/lib/blog-html';

const element = (tagName: string, properties: Record<string, unknown> = {}, children: unknown[] = []) =>
  ({ type: 'element', tagName, properties, children });

const check = (tree: unknown) => rehypeStaticHtml()(tree as Parameters<ReturnType<typeof rehypeStaticHtml>>[0]);

test('static figures written as HTML pass the post check', () => {
  const figure = element('figure', { className: ['fig'] }, [
    element('div', { className: ['fig-canvas'], role: 'img', ariaLabel: 'A diagram' }, [
      element('svg', {}, [element('path', { className: ['fig-wire'], markerEnd: 'url(#fig-arrow)' })]),
      element('div', { className: ['fig-box'] }, [{ type: 'text', value: 'Worker' }]),
    ]),
  ]);
  assert.doesNotThrow(() => check({ type: 'root', children: [figure] }));
});

test('scripts, styles, frames and event handlers are rejected in posts', () => {
  for (const tag of ['script', 'style', 'iframe', 'object', 'embed', 'link']) {
    assert.throws(() => check({ type: 'root', children: [element('div', {}, [element(tag)])] }), new RegExp(`<${tag}>`));
  }
  assert.throws(
    () => check({ type: 'root', children: [element('div', { onClick: 'alert(1)' })] }),
    /event handler/,
  );
});
