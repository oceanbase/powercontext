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

import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { readBlogContent } from '../src/lib/blog-content';

const content = await readBlogContent(path.resolve('..'));
const directory = path.resolve('content/blog');
await rm(directory, { recursive: true, force: true });
for (const lang of ['en', 'zh'] as const) {
  await mkdir(path.join(directory, lang), { recursive: true });
  for (const post of content[lang].posts) {
    await writeFile(path.join(directory, lang, `${post.slug}.md`),
      `---\ntitle: ${JSON.stringify(post.title)}\ndescription: ${JSON.stringify(post.description)}\n---\n${post.body}`);
  }
}
await mkdir('.generated', { recursive: true });
await writeFile('.generated/blog.json', JSON.stringify(content, null, 2));
console.log(`Prepared ${content.en.posts.length} bilingual blog posts.`);
