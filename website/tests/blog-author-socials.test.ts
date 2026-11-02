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
import { AuthorSocialLinks } from '../src/components/blog-author-socials';
import type { BlogAuthor } from '../src/lib/blog-content';

test('author social accounts render platform icons and linked handles', () => {
  const author: BlogAuthor = { id: 'example', name: 'Example', description: '', avatar: '', socials: { github: 'example', x: 'example_dev' } };
  const markup = renderToStaticMarkup(createElement(AuthorSocialLinks, { author }));
  for (const [platform, domain, handle] of [['GitHub', 'github.com', 'example'], ['X', 'x.com', 'example_dev']]) {
    assert.ok(markup.includes(`href="https://${domain}/${handle}"`));
    assert.ok(markup.includes(`aria-label="${platform}: @${handle}"`));
    assert.match(markup, new RegExp(`<svg[^>]*>[\\s\\S]*?</svg><span>@${handle}</span>`));
  }
  assert.equal(renderToStaticMarkup(createElement(AuthorSocialLinks, { author: { ...author, socials: {} } })), '');
});
