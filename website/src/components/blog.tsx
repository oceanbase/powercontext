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
import { blog } from '@/lib/blog';
import type { BlogAuthor, BlogLanguage, BlogPost } from '@/lib/blog-content';
import { withBasePath } from '@/lib/urls';
import styles from './blog.module.css';

export function AuthorPhoto({ author, large = false }: { author: BlogAuthor; large?: boolean }) {
  const src = author.avatar.startsWith('assets/') ? withBasePath(`/docs-assets/${author.avatar.slice(7)}`) : author.avatar;
  return <img className={large ? styles.authorPhoto : styles.avatar} src={src} width={large ? 64 : 32} height={large ? 64 : 32} alt={author.name} loading="lazy" />;
}

export function CoverImage({ post, priority = false }: { post: BlogPost; priority?: boolean }) {
  if (!post.cover) return null;
  return <span className={styles.coverFrame}>
    <span className={styles.coverBrand}>
      <img src={withBasePath('/powercontext-color.png')} alt="PowerContext" width={1696} height={240} decoding="async" />
    </span>
    <img className={styles.coverImage} src={withBasePath(`/docs-assets/${post.cover.slice(7)}`)}
      alt={post.coverAlt} width={1600} height={900} loading={priority ? 'eager' : 'lazy'} decoding="async" />
  </span>;
}

export function Byline({ post, lang }: { post: BlogPost; lang: BlogLanguage }) {
  return <>{post.authors.map((id) => {
    const author = blog[lang].authors[id];
    return <Link key={id} href={`/${lang}/blog/authors/${id}`}><AuthorPhoto author={author} /><span>{author.name}</span></Link>;
  })}</>;
}

export function AuthorCard({ author, lang }: { author: BlogAuthor; lang: BlogLanguage }) {
  return <div className={styles.authorCard}>
    <AuthorPhoto author={author} />
    <div className={styles.authorIdentity}>
      <Link href={`/${lang}/blog/authors/${author.id}`}>{author.name}</Link>
      {author.description && <p>{author.description}</p>}
      <AuthorSocialLinks author={author} className={styles.authorSocials} />
    </div>
  </div>;
}

export function BlogPagination({ base, page, pages, lang }: { base: string; page: number; pages: number; lang: BlogLanguage }) {
  if (pages <= 1) return null;
  const href = (number: number) => number === 1 ? base : `${base}/page/${number}`;
  const numbers = Array.from({ length: pages }, (_, i) => i + 1).filter((number) => number === 1 || number === pages || Math.abs(number - page) <= 2);
  return <nav className={styles.pagination} aria-label={lang === 'zh' ? '博客分页' : 'Blog pagination'}>
    {page > 1 && <Link href={href(page - 1)} rel="prev">{lang === 'zh' ? '上一页' : 'Previous'}</Link>}
    {numbers.map((number, index) => <span key={number} style={{ padding: 0, gap: 8 }}>
      {index > 0 && number - numbers[index - 1] > 1 && <span aria-hidden="true">…</span>}
      {number === page ? <span aria-current="page">{number}</span> : <Link href={href(number)} aria-label={lang === 'zh' ? `第 ${number} 页` : `Page ${number}`}>{number}</Link>}
    </span>)}
    {page < pages && <Link href={href(page + 1)} rel="next">{lang === 'zh' ? '下一页' : 'Next'}</Link>}
  </nav>;
}

export function PostDate({ date, lang }: { date: string; lang: BlogLanguage }) {
  return <time className={styles.date} dateTime={date}>{new Intl.DateTimeFormat(lang === 'zh' ? 'zh-CN' : 'en-US', {
    year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC',
  }).format(new Date(`${date}T00:00:00Z`))}</time>;
}

export function BlogHighlights({ posts, lang }: { posts: BlogPost[]; lang: BlogLanguage }) {
  const [lead, ...others] = posts.slice(0, 3);
  if (!lead) return null;
  const root = `/${lang}/blog`;
  return <section className={styles.highlights} aria-labelledby="latest-heading">
    <div className={styles.sectionBar}>
      <h2 id="latest-heading">{lang === 'zh' ? '最新文章' : 'Latest stories'}</h2>
      <Link href="#all-posts">{lang === 'zh' ? '浏览全部文章' : 'Browse all articles'} <span aria-hidden="true">↘</span></Link>
    </div>
    <div className={styles.featureGrid}>
      <article className={styles.lead}>
        <Link className={styles.coverLink} href={`${root}/${lead.slug}`} aria-label={lead.title}><CoverImage post={lead} priority /></Link>
        <div className={styles.featureMeta}><span>{lead.categories.join(' · ')}</span><PostDate date={lead.date} lang={lang} />{lead.pin && <span>{lang === 'zh' ? '置顶' : 'Pinned'}</span>}</div>
        <h3><Link href={`${root}/${lead.slug}`}>{lead.title}</Link></h3>
        <p>{lead.description}</p>
        <div className={styles.featureBottom}><div className={styles.byline}><Byline post={lead} lang={lang} /></div><Link className={styles.read} href={`${root}/${lead.slug}`}>{lang === 'zh' ? '阅读全文' : 'Read story'} <span aria-hidden="true">↗</span></Link></div>
      </article>
      {others.length > 0 && <div className={styles.secondaryStories}>{others.map((post) => <article key={post.slug} className={styles.secondaryStory}>
        <Link className={styles.coverLink} href={`${root}/${post.slug}`} aria-label={post.title}><CoverImage post={post} /></Link>
        <div className={styles.featureMeta}><span>{post.categories.join(' · ')}</span><PostDate date={post.date} lang={lang} /></div>
        <h3><Link href={`${root}/${post.slug}`}>{post.title}</Link></h3>
        <p>{post.description}</p>
        <div className={styles.byline}><Byline post={post} lang={lang} /></div>
      </article>)}</div>}
    </div>
  </section>;
}
