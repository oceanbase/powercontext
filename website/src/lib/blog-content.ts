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

import { access, readFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { parse } from 'yaml';

export type BlogLanguage = 'en' | 'zh';
export type AuthorSocials = Partial<Record<'github' | 'x', string>>;
export type BlogAuthor = { id: string; name: string; description: string; avatar: string; socials: AuthorSocials };

function authorSocials(value: unknown, id: string): AuthorSocials {
  if (value === undefined) return {};
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`Blog: invalid socials for ${id}`);
  const socials: AuthorSocials = {};
  for (const [platform, raw] of Object.entries(value)) {
    if (platform !== 'github' && platform !== 'x') throw new Error(`Blog: unsupported social platform ${platform}`);
    const handle = requiredText(raw, `${id}: ${platform}`).replace(/^@/, '');
    const valid = platform === 'github' ? /^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$/.test(handle) && !handle.includes('--') : /^[a-zA-Z0-9_]{1,15}$/.test(handle);
    if (!valid) throw new Error(`Blog: invalid ${platform} handle for ${id}`);
    socials[platform] = handle;
  }
  return socials;
}
export type BlogPost = {
  slug: string;
  title: string;
  description: string;
  date: string;
  updated?: string;
  cover?: string;
  coverAlt?: string;
  authors: string[];
  categories: string[];
  draft: boolean;
  pin: boolean;
  body: string;
};
export type BlogContent = Record<BlogLanguage, { posts: BlogPost[]; authors: Record<string, BlogAuthor> }>;
export const postsPerPage = 10;

function requiredText(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`Blog: missing ${field}`);
  return value.trim();
}

function dateText(value: unknown, field: string): string {
  const text = requiredText(value, field);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text) || new Date(text).toISOString().slice(0, 10) !== text) {
    throw new Error(`Blog: invalid ${field}: ${text}`);
  }
  return text;
}

export function parsePost(markdown: string, file: string): BlogPost {
  const match = markdown.replace(/\r\n/g, '\n').match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!match) throw new Error(`Blog: missing front matter in ${file}`);
  const meta = parse(match[1]);
  const slug = requiredText(meta.slug, `${file}: slug`);
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug) || ['page', 'authors', 'archive', 'publishing'].includes(slug)) {
    throw new Error(`Blog: invalid or reserved slug in ${file}`);
  }
  if (!Array.isArray(meta.authors) || (!meta.authors.length && meta.draft !== true) || meta.authors.some((id: unknown) => typeof id !== 'string')) {
    throw new Error(`Blog: authors required in ${file}`);
  }
  for (const flag of ['draft', 'pin']) {
    if (meta[flag] !== undefined && typeof meta[flag] !== 'boolean') throw new Error(`Blog: ${flag} must be boolean`);
  }
  if (meta.categories !== undefined && (!Array.isArray(meta.categories) || meta.categories.some((item: unknown) => typeof item !== 'string'))) {
    throw new Error(`Blog: categories must be a list in ${file}`);
  }
  const created = typeof meta.date === 'object' ? meta.date.created : meta.date;
  const updated = meta.updated ?? (typeof meta.date === 'object' ? meta.date.updated : undefined);
  const date = dateText(created, `${file}: date`);
  if (updated && dateText(updated, `${file}: updated`) < date) throw new Error(`Blog: updated precedes publication in ${file}`);
  const cover = meta.cover === undefined ? undefined : requiredText(meta.cover, `${file}: cover`);
  if (cover && (!/^assets\/blog\/[a-zA-Z0-9/_-]+\.(png|jpe?g|webp|avif)$/.test(cover))) {
    throw new Error(`Blog: cover must be a local image under assets/blog/: ${file}`);
  }
  const coverAlt = cover ? requiredText(meta.cover_alt, `${file}: cover_alt`) : undefined;
  return {
    ...(cover ? { cover, coverAlt } : {}),
    slug, title: requiredText(meta.title, `${file}: title`),
    description: requiredText(meta.description, `${file}: description`),
    date, ...(updated ? { updated } : {}), authors: meta.authors,
    categories: meta.categories ?? [], draft: meta.draft ?? false, pin: meta.pin ?? false,
    body: match[2].replace(/^\s*# [^\n]+\n/, '').replace(/<!--\s*more\s*-->/g, '')
      .replace(/\]\((?:\.\.\/)+assets\//g, '](/docs-assets/'),
  };
}

export function publishedPosts(posts: BlogPost[], today = new Date().toISOString().slice(0, 10)) {
  return posts.filter((post) => !post.draft && post.date <= today).sort((a, b) =>
    Number(b.pin) - Number(a.pin) || b.date.localeCompare(a.date) || a.slug.localeCompare(b.slug));
}

export function paginatePosts(posts: BlogPost[], page = 1) {
  const pages = Math.max(1, Math.ceil(posts.length / postsPerPage));
  if (!Number.isInteger(page) || page < 1 || page > pages) return undefined;
  return { posts: posts.slice((page - 1) * postsPerPage, page * postsPerPage), page, pages, total: posts.length };
}

export async function readBlogContent(repositoryDir: string, today?: string): Promise<BlogContent> {
  const result = {} as BlogContent;
  const filesByLanguage: Record<string, string[]> = {};
  for (const lang of ['en', 'zh'] as const) {
    const directory = path.join(repositoryDir, 'docs', lang, 'blog');
    const rawAuthors = parse(await readFile(path.join(directory, '.authors.yml'), 'utf8')).authors;
    const authors: Record<string, BlogAuthor> = {};
    for (const [id, raw] of Object.entries(rawAuthors) as [string, Record<string, unknown>][]) {
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id)) throw new Error(`Blog: invalid author ID ${id}`);
      const avatar = requiredText(raw.avatar, `${id}: avatar`);
      if (!avatar.startsWith('assets/blog/') && !avatar.startsWith('https://')) throw new Error(`Blog: invalid avatar ${avatar}`);
      authors[id] = { id, name: requiredText(raw.name, `${id}: name`), description: raw.description === undefined ? '' : requiredText(raw.description, `${id}: description`), avatar, socials: authorSocials(raw.socials, id) };
    }
    const files = (await readdir(path.join(directory, 'posts'), { recursive: true }))
      .filter((file) => file.endsWith('.md')).sort();
    filesByLanguage[lang] = files;
    const posts = await Promise.all(files.map(async (file) => parsePost(await readFile(path.join(directory, 'posts', file), 'utf8'), file)));
    if (new Set(posts.map((post) => post.slug)).size !== posts.length) throw new Error(`Blog: duplicate slug in ${lang}`);
    for (const post of posts) for (const id of post.authors) {
      if (!authors[id]) throw new Error(`Blog: unknown author ${id} in ${post.slug}`);
    }
    for (const post of posts) {
      if (!post.draft && !post.cover) throw new Error(`Blog: cover required before publication: ${post.slug}`);
      if (post.cover) {
        try { await access(path.join(repositoryDir, 'docs', post.cover)); }
        catch { throw new Error(`Blog: cover file missing: ${post.cover}`); }
      }
    }
    result[lang] = { authors, posts };
  }
  if (JSON.stringify(filesByLanguage.en) !== JSON.stringify(filesByLanguage.zh)) throw new Error('Blog: translated post paths must match');
  if (JSON.stringify(Object.keys(result.en.authors).sort()) !== JSON.stringify(Object.keys(result.zh.authors).sort())) throw new Error('Blog: author IDs must match');
  for (const id of Object.keys(result.en.authors)) {
    for (const platform of ['github', 'x'] as const) {
      if (result.en.authors[id].socials[platform] !== result.zh.authors[id].socials[platform]) throw new Error(`Blog: author socials must match: ${id}: ${platform}`);
    }
    if (result.en.authors[id].avatar !== result.zh.authors[id].avatar) throw new Error(`Blog: author photos must match: ${id}`);
  }
  for (let index = 0; index < result.en.posts.length; index++) {
    const en = result.en.posts[index];
    const zh = result.zh.posts[index];
    for (const key of ['slug', 'date', 'updated', 'authors', 'draft', 'pin', 'cover'] as const) {
      if (JSON.stringify(en[key]) !== JSON.stringify(zh[key])) throw new Error(`Blog: ${key} must match for ${en.slug}`);
    }
  }
  for (const lang of ['en', 'zh'] as const) result[lang].posts = publishedPosts(result[lang].posts, today);
  return result;
}
