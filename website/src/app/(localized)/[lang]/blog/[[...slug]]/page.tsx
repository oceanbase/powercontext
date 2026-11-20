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
import { notFound } from 'next/navigation';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { AuthorSocialLinks } from '@/components/blog-author-socials';
import { SiteFooter } from '@/components/site-footer';
import {
  AuthorCards, AuthorPhoto, BlogPagination, Byline, CoverImage, FeaturedPost, PostList,
} from '@/components/blog';
import { BackLink, Dateline, PageHeading, editorial as styles } from '@/components/editorial';
import figures from '@/components/figures.module.css';
import { CopyPageButton } from '@/components/copy-page-button';
import { FigureDefs } from '@/components/figure-defs';
import { getMDXComponents } from '@/components/mdx';
import { SegmentedNav } from '@/components/segmented-nav';
import { blog, blogListing, blogRoutes } from '@/lib/blog';
import { blogSource } from '@/lib/blog-source';
import { isLanguage, languages } from '@/lib/i18n';
import { baseOptions } from '@/lib/site';
import { absoluteSiteUrl, withBasePath } from '@/lib/urls';
import { pageMetadata } from '@/lib/metadata';
import { getPageMarkdownUrl } from '@/lib/markdown';
import { StructuredData } from '@/components/structured-data';

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
  const baseTitle = post?.title ?? listing?.author?.name
    ?? (listing?.year ? `${lang === 'zh' ? '归档' : 'Archive'} · ${listing.year}` : lang === 'zh' ? '博客' : 'Blog');
  const title = listing && listing.page > 1
    ? `${baseTitle} · ${lang === 'zh' ? `第 ${listing.page} 页` : `Page ${listing.page}`}` : baseTitle;
  const description = post?.description ?? (listing?.author
    ? (lang === 'zh'
      ? `${listing.author.name} 在 PowerContext 博客发表的文章。${listing.author.description}`
      : `Articles by ${listing.author.name} on the PowerContext blog. ${listing.author.description}`).trim()
    : listing?.year
      ? (lang === 'zh' ? `${listing.year} 年发表的 PowerContext 博客文章。` : `PowerContext blog posts published in ${listing.year}.`)
      : (lang === 'zh' ? '项目记忆、Agent 协作和 PowerContext 开发实践的文章。' : 'Articles about project memory, agent collaboration, and building PowerContext.'));
  const suffix = slug.length ? `/${slug.join('/')}` : '';
  return pageMetadata({
    path: `/${lang}/blog${suffix}/`, lang, title,
    description,
    translations: Object.fromEntries(languages.filter((locale) =>
      blogRoutes(locale).some((route) => route.join('/') === slug.join('/')),
    ).map((locale) => [locale, `/${locale}/blog${suffix}/`])),
    ...(post ? {
      markdown: getPageMarkdownUrl({ locale: lang, slugs: ['blog', post.slug] }).url,
      image: post.cover ? { url: `/docs-assets/${post.cover.slice(7)}`, alt: post.coverAlt } : undefined,
      article: { publishedTime: post.date, modifiedTime: post.updated ?? post.date,
        authors: post.authors.map((id) => absoluteSiteUrl(`/${lang}/blog/authors/${id}/`)) },
    } : {}),
  });
}

const text = {
  en: {
    blog: 'Blog',
    lead: 'Ideas and practical guides on project memory, agent collaboration, and engineering.',
    allPosts: 'All posts',
    earlier: 'Earlier posts',
    morePosts: 'More posts',
    archive: 'Archive',
    years: 'Browse by year',
    all: 'All',
    contents: 'Contents',
    count: (n: number) => `${n} ${n === 1 ? 'post' : 'posts'}`,
    empty: 'Articles are coming soon.',
  },
  zh: {
    blog: '博客',
    lead: '关于项目记忆、Agent 协作与工程实践的思考和记录。',
    allPosts: '全部文章',
    earlier: '更早的文章',
    morePosts: '更多文章',
    archive: '归档',
    years: '按年份查看',
    all: '全部',
    contents: '目录',
    count: (n: number) => `共 ${n} 篇`,
    empty: '文章即将发布。',
  },
} as const;

/** Articles with fewer sections read fine without a contents list. */
const MIN_TOC_HEADINGS = 3;
/** How many other posts follow an article. */
const MORE_POSTS = 3;

export default async function BlogPage({ params }: Props) {
  const { lang, slug = [] } = await params;
  if (!isLanguage(lang)) notFound();
  const copy = text[lang];
  const root = `/${lang}/blog`;
  const post = slug.length === 1 ? blog[lang].posts.find((item) => item.slug === slug[0]) : undefined;
  if (post) {
    const source = blogSource.getPage([post.slug], lang);
    if (!source) notFound();
    const Body = source.data.body;
    const markdownUrl = withBasePath(getPageMarkdownUrl({ ...source, slugs: ['blog', ...source.slugs] }).url);
    const headings = source.data.toc.filter((item) => item.depth === 2);
    const more = blog[lang].posts.filter((item) => item.slug !== post.slug).slice(0, MORE_POSTS);
    return <HomeLayout {...baseOptions(lang)}>
      <StructuredData data={{
        '@context': 'https://schema.org', '@type': 'BlogPosting',
        headline: post.title, description: post.description,
        url: absoluteSiteUrl(`${root}/${post.slug}/`),
        mainEntityOfPage: absoluteSiteUrl(`${root}/${post.slug}/`), inLanguage: lang,
        datePublished: post.date, dateModified: post.updated ?? post.date,
        ...(post.cover ? { image: absoluteSiteUrl(`/docs-assets/${post.cover.slice(7)}`) } : {}),
        author: post.authors.map((id) => ({
          '@type': 'Person', name: blog[lang].authors[id].name,
          url: absoluteSiteUrl(`${root}/authors/${id}/`),
        })),
      }} />
      <StructuredData data={{
        '@context': 'https://schema.org', '@type': 'BreadcrumbList',
        itemListElement: [
          { name: 'PowerContext', item: absoluteSiteUrl(lang === 'en' ? '/' : '/zh/') },
          { name: copy.blog, item: absoluteSiteUrl(`${root}/`) },
          { name: post.title, item: absoluteSiteUrl(`${root}/${post.slug}/`) },
        ].map((item, index) => ({ '@type': 'ListItem', position: index + 1, ...item })),
      }} />
      <main>
        <article className={styles.article}>
          <header className={styles.articleHeader}>
            <BackLink href={root}>{copy.blog}</BackLink>
            <Dateline date={post.date} labels={[post.categories.join(' · ') || null]} lang={lang} />
            <h1 className={styles.postTitle}>{post.title}</h1>
            <p className={styles.postSummary}>{post.description}</p>
            <div className={styles.bylineRow}>
              <Byline lang={lang} post={post} />
              <CopyPageButton lang={lang} markdownUrl={markdownUrl} />
            </div>
          </header>
          <div className={styles.postCover}><CoverImage post={post} priority /></div>
          {headings.length >= MIN_TOC_HEADINGS ? (
            <nav aria-labelledby="post-contents" className={styles.toc}>
              <h2 id="post-contents">{copy.contents}</h2>
              <ol>
                {headings.map((heading) => <li key={heading.url}><a href={heading.url}>{heading.title}</a></li>)}
              </ol>
            </nav>
          ) : null}
          <div className={`${styles.prose} ${figures.scope}`}>
            <Body components={getMDXComponents()} />
            <FigureDefs />
          </div>
          <AuthorCards lang={lang} post={post} />
        </article>
        {more.length > 0 ? (
          <section aria-labelledby="more-posts" className={`${styles.main} ${styles.section} pt-0!`}>
            <h2 className={styles.sectionTitle} id="more-posts">{copy.morePosts}</h2>
            <PostList lang={lang} posts={more} />
          </section>
        ) : null}
      </main>
      <SiteFooter lang={lang} />
    </HomeLayout>;
  }
  const listing = blogListing(lang, slug);
  if (!listing) notFound();
  const years = [...new Set(blog[lang].posts.map((item) => item.date.slice(0, 4)))].sort().reverse();
  const isFront = slug.length === 0;
  const [featured, ...rest] = listing.posts;
  const rows = isFront && featured ? rest : listing.posts;
  const title = listing.author?.name ?? (listing.year ? `${copy.archive} · ${listing.year}` : copy.blog);
  return <HomeLayout {...baseOptions(lang)}>
    <main className={styles.main}>
      {listing.author ? (
        <header className={styles.profile}>
          <BackLink href={root}>{copy.allPosts}</BackLink>
          <AuthorPhoto author={listing.author} large />
          <h1 className={styles.title}>{listing.author.name}</h1>
          {listing.author.description ? <p className={styles.lead}>{listing.author.description}</p> : null}
          <AuthorSocialLinks author={listing.author} className={styles.socials} />
        </header>
      ) : (
        <PageHeading align="split" lead={listing.year ? undefined : copy.lead} title={title}>
          {slug.length > 0 ? <BackLink href={root}>{copy.allPosts}</BackLink> : null}
        </PageHeading>
      )}
      {isFront && featured ? <FeaturedPost lang={lang} post={featured} /> : null}
      <section aria-labelledby="all-posts-heading" className={styles.section} id="all-posts">
        <div className={styles.sectionHead}>
          <h2 className={styles.sectionTitle} id="all-posts-heading">
            {isFront && featured ? copy.earlier : copy.allPosts}
            <span className="sr-only"> · {copy.count(listing.total)}</span>
          </h2>
          {!listing.author && years.length > 1 ? (
            <SegmentedNav
              current={listing.year ? `${root}/archive/${listing.year}` : root}
              items={[{ href: root, label: copy.all }, ...years.map((year) => ({ href: `${root}/archive/${year}`, label: year }))]}
              label={copy.years}
            />
          ) : null}
        </div>
        {rows.length > 0 ? <PostList lang={lang} posts={rows} /> : null}
        {listing.total === 0 ? <p className={styles.lead}>{copy.empty}</p> : null}
        <BlogPagination {...listing} lang={lang} />
      </section>
    </main>
    <SiteFooter lang={lang} />
  </HomeLayout>;
}
