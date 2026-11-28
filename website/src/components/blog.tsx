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

import Link from 'next/link';
import { AuthorSocialLinks } from './blog-author-socials';
import { Dateline, EntryRow, editorial as styles } from './editorial';
import { blog } from '@/lib/blog';
import type { BlogAuthor, BlogLanguage, BlogPost } from '@/lib/blog-content';
import { withBasePath } from '@/lib/urls';

const copy = {
  en: { pinned: 'Pinned', page: (n: number) => `Page ${n}`, pagination: 'Blog pagination', previous: 'Previous', next: 'Next' },
  zh: { pinned: '置顶', page: (n: number) => `第 ${n} 页`, pagination: '博客分页', previous: '上一页', next: '下一页' },
} as const;

function authorPath(lang: BlogLanguage, id: string) {
  return `/${lang}/blog/authors/${id}`;
}

function avatarSource(author: BlogAuthor) {
  return author.avatar.startsWith('assets/') ? withBasePath(`/docs-assets/${author.avatar.slice(7)}`) : author.avatar;
}

/** An avatar beside a visible name, so it stays out of the accessibility tree. */
export function AuthorPhoto({ author, large = false }: { author: BlogAuthor; large?: boolean }) {
  const size = large ? 64 : 24;
  return <img alt="" className={large ? styles.avatarLarge : styles.avatar} decoding="async"
    height={size} loading="lazy" src={avatarSource(author)} width={size} />;
}

export function CoverImage({ post, priority = false, small = false }: {
  post: BlogPost;
  priority?: boolean;
  small?: boolean;
}) {
  if (!post.cover) return null;
  return <span className={styles.cover} data-size={small ? 'small' : undefined}>
    <span className={styles.coverBrand}>
      <img alt="PowerContext" decoding="async" height={240} src={withBasePath('/powercontext-color.png')} width={1696} />
    </span>
    <img alt={small ? '' : post.coverAlt} className={styles.coverImage} decoding="async" height={900}
      loading={priority ? 'eager' : 'lazy'} src={withBasePath(`/docs-assets/${post.cover.slice(7)}`)} width={1600} />
  </span>;
}

function labelsFor(post: BlogPost, lang: BlogLanguage) {
  return [post.categories.join(' · ') || null, post.pin ? copy[lang].pinned : null];
}

/** Authors in a list: overlapping avatars, then the names in one quiet line. */
export function AuthorSummary({ post, lang }: { post: BlogPost; lang: BlogLanguage }) {
  const authors = post.authors.map((id) => blog[lang].authors[id]);
  if (!authors.length) return null;
  return <span className={styles.authors}>
    <span className={styles.avatarGroup}>{authors.map((author) => <AuthorPhoto author={author} key={author.id} />)}</span>
    <span>{new Intl.ListFormat(lang === 'zh' ? 'zh-CN' : 'en', { type: 'conjunction' }).format(authors.map((author) => author.name))}</span>
  </span>;
}

/** A post byline: each author's avatar and name, linked to their page. */
export function Byline({ post, lang }: { post: BlogPost; lang: BlogLanguage }) {
  return <address className={`${styles.byline} not-italic`}>
    {post.authors.map((id) => {
      const author = blog[lang].authors[id];
      return <Link href={authorPath(lang, id)} key={id}><AuthorPhoto author={author} />{author.name}</Link>;
    })}
  </address>;
}

/** The authors after an article: who they are and where to find them. */
export function AuthorCards({ post, lang }: { post: BlogPost; lang: BlogLanguage }) {
  if (!post.authors.length) return null;
  return <section aria-labelledby="post-authors" className={styles.authorsFooter} data-count={post.authors.length}>
    <h2 id="post-authors">{lang === 'zh' ? '关于作者' : post.authors.length > 1 ? 'About the authors' : 'About the author'}</h2>
    {post.authors.map((id) => {
      const author = blog[lang].authors[id];
      return <div className={styles.authorCard} key={id}>
        <AuthorPhoto author={author} />
        <div>
          <Link className={styles.authorName} href={authorPath(lang, id)}>{author.name}</Link>
          {author.description ? <p className={styles.authorRole}>{author.description}</p> : null}
          <AuthorSocialLinks author={author} className={styles.socials} />
        </div>
      </div>;
    })}
  </section>;
}

/**
 * The newest post, given the room of a magazine lead: its cover beside the
 * title, description and authors.
 */
export function FeaturedPost({ post, lang }: { post: BlogPost; lang: BlogLanguage }) {
  const href = `/${lang}/blog/${post.slug}`;
  return <article className={`${styles.featured} ${styles.entry}`}>
    <Link aria-hidden="true" href={href} tabIndex={-1}><CoverImage post={post} priority /></Link>
    <div className={styles.featuredBody}>
      <div className={styles.metaRow}>
        <AuthorSummary lang={lang} post={post} />
        <Dateline date={post.date} labels={labelsFor(post, lang)} lang={lang} />
      </div>
      <h2 className={styles.featuredTitle}><Link className={styles.linkTitle} href={href}>{post.title}</Link></h2>
      <p className={styles.featuredLead}>{post.description}</p>
    </div>
  </article>;
}

/** Posts as rows, newest first: authors, date and category on one line, the post, then its cover. */
export function PostList({ posts, lang }: { posts: BlogPost[]; lang: BlogLanguage }) {
  return <ul className={styles.list}>
    {posts.map((post) => <li key={post.slug}>
      <EntryRow byline={<AuthorSummary lang={lang} post={post} />} date={post.date} href={`/${lang}/blog/${post.slug}`}
        labels={labelsFor(post, lang)} lang={lang} thumb={<CoverImage post={post} small />} title={post.title}>
        <p className={styles.rowText}>{post.description}</p>
      </EntryRow>
    </li>)}
  </ul>;
}

export function BlogPagination({ base, page, pages, lang }: { base: string; page: number; pages: number; lang: BlogLanguage }) {
  if (pages <= 1) return null;
  const text = copy[lang];
  const href = (number: number) => number === 1 ? base : `${base}/page/${number}`;
  const numbers = Array.from({ length: pages }, (_, i) => i + 1)
    .filter((number) => number === 1 || number === pages || Math.abs(number - page) <= 2);
  return <nav aria-label={text.pagination} className={styles.pagination}>
    {page > 1 && <Link href={href(page - 1)} rel="prev">{text.previous}</Link>}
    {numbers.map((number, index) => <span className="contents" key={number}>
      {index > 0 && number - numbers[index - 1] > 1 && <span aria-hidden="true" className={styles.ellipsis}>…</span>}
      {number === page
        ? <span aria-current="page">{number}</span>
        : <Link aria-label={text.page(number)} href={href(number)}>{number}</Link>}
    </span>)}
    {page < pages && <Link href={href(page + 1)} rel="next">{text.next}</Link>}
  </nav>;
}
