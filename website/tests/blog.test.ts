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
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { parsePost, publishedPosts, paginatePosts, readBlogContent } from '../src/lib/blog-content';

function article(slug: string, extra = '') {
  return `---\nslug: ${slug}\ntitle: Example ${slug}\ndescription: An example.\ndate: 2020-01-01\ncover: assets/blog/example/cover.png\ncover_alt: A continuous path through project records\nauthors: [ada, bob]\n${extra}---\n# Example\n\nBody with 中文.\n`;
}

test('public posts are sorted and paginated without drafts or future posts', () => {
  const posts = Array.from({ length: 23 }, (_, i) => parsePost(article(`post-${String(i).padStart(2, '0')}`), 'post.md'));
  posts.push(parsePost(article('draft', 'draft: true\n'), 'draft.md'));
  posts.push(parsePost(article('future').replace('2020-01-01', '2099-01-01'), 'future.md'));
  posts.push(parsePost(article('pinned', 'pin: true\n'), 'pinned.md'));
  const visible = publishedPosts(posts, '2026-10-23');
  assert.equal(visible.length, 24);
  assert.equal(visible[0].slug, 'pinned');
  assert.deepEqual([1, 2, 3].map((page) => paginatePosts(visible, page)?.posts.length), [10, 10, 4]);
  assert.equal(new Set([1, 2, 3].flatMap((page) => paginatePosts(visible, page)!.posts.map((post) => post.slug))).size, 24);
  for (const page of [0, 4, -1, 1.5, NaN]) assert.equal(paginatePosts(visible, page), undefined);
  assert.equal(paginatePosts([], 1)?.pages, 1);
});

test('unattributed drafts stay private and require an author before publication', () => {
  const markdown = article('unattributed', 'draft: true\n').replace('[ada, bob]', '[]');
  const post = parsePost(markdown, 'draft.md');
  assert.deepEqual(post.authors, []);
  assert.deepEqual(publishedPosts([post]), []);
  assert.throws(() => parsePost(markdown.replace('draft: true', 'draft: false'), 'post.md'), /authors required/);
});

test('Markdown body preserves Chinese, code and shared image links', () => {
  const post = parsePost(article('sample') + '\n<!-- more -->\n![图](../../../assets/blog/image.png)\n```sh\necho 中文\n```', 'sample.md');
  assert.ok(post.body.includes('Body with 中文.'));
  assert.ok(post.body.includes('```sh\necho 中文\n```'));
  assert.ok(post.body.includes('](/docs-assets/blog/image.png)'));
  assert.ok(!post.body.includes('<!-- more -->'));
  assert.deepEqual(post.authors, ['ada', 'bob']);
});

test('bilingual publishing rejects mismatched dates, unknown authors, and missing covers', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'powercontext-blog-'));
  try {
    await mkdir(path.join(root, 'docs/assets/blog/example'), { recursive: true });
    await writeFile(path.join(root, 'docs/assets/blog/example/cover.png'), Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLttAAAAABJRU5ErkJggg==', 'base64'));
    for (const locale of ['en', 'zh']) {
      const dir = path.join(root, 'docs', locale, 'blog');
      await mkdir(path.join(dir, 'posts'), { recursive: true });
      await writeFile(path.join(dir, '.authors.yml'), 'authors:\n  ada:\n    name: Ada\n    socials:\n      github: "@ada"\n      x: ada_dev\n    avatar: assets/blog/ada.png\n  bob:\n    name: Bob\n    description: Engineer\n    avatar: assets/blog/bob.png\n');
      await writeFile(path.join(dir, 'posts/example.md'), article('example'));
    }
    const content = await readBlogContent(root);
    assert.equal(content.zh.authors.ada.avatar, content.en.authors.ada.avatar);
    assert.equal(content.zh.posts[0].authors.length, 2);
    assert.equal(content.en.authors.ada.description, '');
    assert.deepEqual(content.en.authors.ada.socials, { github: 'ada', x: 'ada_dev' });
    const authorFile = path.join(root, 'docs/zh/blog/.authors.yml');
    const originalAuthors = await readFile(authorFile, 'utf8');
    await writeFile(authorFile, originalAuthors.replace('@ada', '@someone-else'));
    await assert.rejects(readBlogContent(root), /author socials must match/);
    await writeFile(authorFile, originalAuthors.replace('@ada', 'https://github.com/ada'));
    await assert.rejects(readBlogContent(root), /invalid github handle/);
    await writeFile(authorFile, originalAuthors);
    const zh = path.join(root, 'docs/zh/blog/posts/example.md');
    await writeFile(zh, article('example').replace('2020-01-01', '2020-01-02'));
    await assert.rejects(readBlogContent(root), /date must match/);
    await writeFile(zh, article('example').replace('[ada, bob]', '[missing]'));
    await assert.rejects(readBlogContent(root), /unknown author/);
    await writeFile(zh, article('example').replace('cover: assets/blog/example/cover.png\n', ''));
    await assert.rejects(readBlogContent(root), /cover required/);
    await writeFile(zh, article('example').replace('cover_alt: A continuous path through project records\n', ''));
    await assert.rejects(readBlogContent(root), /cover_alt/);
    await writeFile(zh, article('example').replace('cover.png', 'missing.png'));
    await assert.rejects(readBlogContent(root), /cover file missing/);
    await writeFile(path.join(root, 'docs/assets/blog/example/other.png'), Buffer.from('image'));
    await writeFile(zh, article('example').replace('cover.png', 'other.png'));
    await assert.rejects(readBlogContent(root), /cover must match/);
    await writeFile(zh, article('example'));
    await rm(path.join(root, 'docs/assets/blog/example/cover.png'));
    await assert.rejects(readBlogContent(root), /cover file missing/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('repository blog has matched translations and shared author photos', async () => {
  const content = await readBlogContent(path.resolve('..'));
  assert.deepEqual(content.en.posts.map((post) => post.slug), content.zh.posts.map((post) => post.slug));
});
