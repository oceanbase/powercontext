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

import content from '../../.generated/blog.json';
import { paginatePosts, type BlogAuthor, type BlogContent, type BlogLanguage } from './blog-content';

export const blog = content as BlogContent;

export function blogListing(lang: BlogLanguage, segments: string[] = []) {
  let posts = blog[lang].posts;
  let base = `/${lang}/blog`;
  let author: BlogAuthor | undefined;
  let year: string | undefined;
  let rest = segments;
  if (rest[0] === 'authors' && rest[1]) {
    author = blog[lang].authors[rest[1]];
    if (!author || !posts.some((post) => post.authors.includes(rest[1]))) return undefined;
    posts = posts.filter((post) => post.authors.includes(rest[1]));
    base += `/authors/${author.id}`;
    rest = rest.slice(2);
  } else if (rest[0] === 'archive' && rest[1]) {
    year = rest[1];
    posts = posts.filter((post) => post.date.startsWith(`${rest[1]}-`));
    if (!posts.length) return undefined;
    base += `/archive/${year}`;
    rest = rest.slice(2);
  }
  if (rest.length && (rest.length !== 2 || rest[0] !== 'page' || !/^[2-9]\d*$|^1\d+$/.test(rest[1]))) return undefined;
  const listing = paginatePosts(posts, rest.length ? Number(rest[1]) : 1);
  return listing ? { ...listing, base, author, year } : undefined;
}

export function blogRoutes(lang: BlogLanguage) {
  const posts = blog[lang].posts;
  const routes: string[][] = [[]];
  const groups = [
    { segments: [] as string[], posts },
    ...Object.keys(blog[lang].authors).map((id) => ({ segments: ['authors', id], posts: posts.filter((post) => post.authors.includes(id)) })),
    ...[...new Set(posts.map((post) => post.date.slice(0, 4)))].map((year) => ({ segments: ['archive', year], posts: posts.filter((post) => post.date.startsWith(year)) })),
  ];
  for (const group of groups) {
    if (!group.posts.length) continue;
    if (group.segments.length) routes.push(group.segments);
    for (let page = 2; page <= paginatePosts(group.posts)!.pages; page++) routes.push([...group.segments, 'page', String(page)]);
  }
  routes.push(...posts.map((post) => [post.slug]));
  return routes;
}
