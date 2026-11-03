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

import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { AuthorSocialLinks } from '@/components/blog-author-socials';
import { SiteFooter } from '@/components/site-footer';
import { AuthorCard, AuthorPhoto, BlogHighlights, BlogPagination, Byline, CoverImage, PostDate } from '@/components/blog';
import { getMDXComponents } from '@/components/mdx';
import styles from '@/components/blog.module.css';
import { blog, blogListing, blogRoutes } from '@/lib/blog';
import { blogSource } from '@/lib/blog-source';
import { isLanguage, languages } from '@/lib/i18n';
import { baseOptions } from '@/lib/site';
import { absoluteSiteUrl } from '@/lib/urls';

type Props = { params: Promise<{ lang: string; slug?: string[] }> };

export function generateStaticParams() {
  return languages.flatMap((lang) => blogRoutes(lang).map((slug) => ({ lang, slug })));
}

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang, slug = [] } = await params;
  if (!isLanguage(lang)) notFound();
  const post = slug.length === 1 ? blog[lang].posts.find((item) => item.slug === slug[0]) : undefined;
  const listing = blogListing(lang, slug);
  if (!post && !listing) notFound();
  const title = post?.title ?? listing?.author?.name ?? (lang === 'zh' ? '博客' : 'Blog');
  const suffix = slug.length ? `/${slug.join('/')}` : '';
  return {
    title, description: post?.description ?? (lang === 'zh' ? 'PowerContext 工程实践、产品更新与技术文章。' : 'Engineering stories, product updates, and technical guides from PowerContext.'),
    alternates: {
      canonical: absoluteSiteUrl(`/${lang}/blog${suffix}/`),
      languages: Object.fromEntries(languages.map((locale) => [locale, absoluteSiteUrl(`/${locale}/blog${suffix}/`)])),
    },
    ...(post ? { openGraph: { type: 'article', title: post.title, description: post.description, ...(post.cover ? { images: [{ url: absoluteSiteUrl(`/docs-assets/${post.cover.slice(7)}`), alt: post.coverAlt }] } : {}), publishedTime: post.date, modifiedTime: post.updated ?? post.date, authors: post.authors.map((id) => blog[lang].authors[id].name) } } : {}),
  };
}

export default async function BlogPage({ params }: Props) {
  const { lang, slug = [] } = await params;
  if (!isLanguage(lang)) notFound();
  const root = `/${lang}/blog`;
  const post = slug.length === 1 ? blog[lang].posts.find((item) => item.slug === slug[0]) : undefined;
  if (post) {
    const source = blogSource.getPage([post.slug], lang);
    if (!source) notFound();
    const Body = source.data.body;
    return <HomeLayout {...baseOptions(lang)}>
      <main className={styles.main}><article className={styles.article}>
        <Link className={styles.back} href={root}>← {lang === 'zh' ? '返回博客' : 'Back to blog'}</Link>
        <header className={styles.articleHeading}>
          <div className={styles.featureMeta}><span>{post.categories.join(' · ')}</span><PostDate date={post.date} lang={lang} /></div>
          <h1>{post.title}</h1>
          <div className={styles.authorCards}>{post.authors.map((id) => <AuthorCard key={id} author={blog[lang].authors[id]} lang={lang} />)}</div>
          <div className={styles.articleSummary}><strong>TL;DR</strong><p>{post.description}</p></div>
        </header>
        <div className={styles.articleCover}><CoverImage post={post} priority /></div>
        <div className={`prose max-w-none ${styles.articleBody}`}><Body components={getMDXComponents()} /></div>
      </article></main>
      <SiteFooter lang={lang} />
    </HomeLayout>;
  }
  const listing = blogListing(lang, slug);
  if (!listing) notFound();
  const years = [...new Set(blog[lang].posts.map((item) => item.date.slice(0, 4)))].sort().reverse();
  return <HomeLayout {...baseOptions(lang)}>
    <main className={styles.main}>
      <header className={`${styles.heading} ${listing.author ? styles.authorHeading : ''}`}>
        {slug.length > 0 && <Link className={styles.back} href={root}>← {lang === 'zh' ? '全部文章' : 'All posts'}</Link>}
        {listing.author ? <><AuthorPhoto author={listing.author} large /><h1>{listing.author.name}</h1>{listing.author.description && <p>{listing.author.description}</p>}<AuthorSocialLinks author={listing.author} className={styles.authorSocials} /></> : <>
          <div className={styles.eyebrow}>PowerContext</div>
          <h1>{listing.year ? `${lang === 'zh' ? '归档' : 'Archive'} · ${listing.year}` : lang === 'zh' ? '博客' : 'Blog'}</h1>
          <p>{lang === 'zh' ? '关于项目记忆、Agent 协作与工程实践的思考和记录。' : 'Ideas and practical guides on project memory, agent collaboration, and engineering.'}</p>
        </>}
      </header>
      {slug.length === 0 && <BlogHighlights posts={listing.posts} lang={lang} />}
      <section id="all-posts" className={styles.allPosts} aria-labelledby="all-posts-heading">
      <div className={styles.toolbar}>
        <div><h2 id="all-posts-heading">{lang === 'zh' ? '全部文章' : 'All articles'}</h2><span>{lang === 'zh' ? `共 ${listing.total} 篇` : `${listing.total} ${listing.total === 1 ? 'article' : 'articles'}`}</span></div>
        <nav className={styles.years} aria-label={lang === 'zh' ? '按年份查看' : 'Browse by year'}><span>{lang === 'zh' ? '年份' : 'Year'}</span>{years.map((year) => <Link key={year} href={`${root}/archive/${year}`} aria-current={listing.year === year ? 'page' : undefined}>{year}</Link>)}</nav>
      </div>
      <div className={styles.columnLabels} aria-hidden="true"><span>{lang === 'zh' ? '文章 / 作者' : 'Article / Author'}</span><span>{lang === 'zh' ? '摘要' : 'Description'}</span><span>{lang === 'zh' ? '分类' : 'Category'}</span><span>{lang === 'zh' ? '发布日期' : 'Published'}</span></div>
      {listing.posts.map((item) => <article key={item.slug} className={styles.row}>
        <div className={styles.rowTitle}>
          <Link className={styles.rowCover} href={`${root}/${item.slug}`} aria-label={item.title}><CoverImage post={item} /></Link>
          {item.pin && <span className={styles.pinned}>{lang === 'zh' ? '置顶' : 'Pinned'}</span>}
          <h3><Link href={`${root}/${item.slug}`}>{item.title}<span className={styles.rowArrow} aria-hidden="true"> ↗</span></Link></h3>
          <div className={styles.byline}><Byline post={item} lang={lang} /></div>
        </div>
        <p className={styles.summary}>{item.description}</p>
        <div className={styles.categories}>{item.categories.join(' · ')}</div>
        <PostDate date={item.date} lang={lang} />
      </article>)}
      {listing.total === 0 && <p className={styles.summary}>{lang === 'zh' ? '文章即将发布。' : 'Articles are coming soon.'}</p>}
      <BlogPagination {...listing} lang={lang} />
      </section>
    </main><SiteFooter lang={lang} />
  </HomeLayout>;
}
