# Publishing blog posts

Send the article as a Markdown file or Markdown text. The publishing workflow prepares the English and Chinese editions, derives a stable slug and summary, reuses known author profiles, and checks both editions together. A new author needs a real name, short biography, and photo; these facts are never invented. Preparing a post does not publish the website.

When adapting an article, remove source-channel promotions for following accounts, joining groups, or subscribing, along with their QR codes. Keep relevant technical links and source references.

## Files

- English: `docs/en/blog/posts/<slug>.md`
- Chinese: `docs/zh/blog/posts/<slug>.md`
- Author profiles: `docs/en/blog/.authors.yml` and `docs/zh/blog/.authors.yml`
- Shared photos and article images: `docs/assets/blog/`

Both editions use matching relative paths, slugs, publication/update dates, author IDs, draft status, and pin status. Titles, summaries, article text, categories, and biographies are localized. Code, commands, URLs, and technical meaning remain faithful to the original.

```yaml
---
title: An article title
description: A short summary for the article list.
slug: article-slug
date: 2026-10-23
updated: 2026-10-24
authors:
  - jackie-cqz
categories:
  - Engineering
cover: assets/blog/article-slug/cover.png
cover_alt: Illustration of project context carried across sessions
draft: true
pin: false
---
```

Write ordinary Markdown below the front matter. A leading H1 is optional; the website renders the title once. Images can use `../../../assets/blog/article-slug/image.png`. Author photo paths use `assets/blog/authors/name.jpg`. Register each author under an independent ID with `name`, `avatar`, and an optional `description`. Put any organization, team affiliation, or contributor role in that author’s `description`; there is no shared team affiliation. Drafts awaiting attribution may use `authors: []`; publication requires an identified author ID.

## Author social accounts

Store social handles under `socials`, separately from the optional biography. The article header and author page display the platform icon followed by a clickable `@handle`. Configure only verified accounts; omit unknown accounts. Both languages must use the same handles. Supply a handle, not a full URL; a leading `@` is optional.

```yaml
authors:
  jingshun:
    name: Tanner
    avatar: assets/blog/authors/jingshun.jpg
    socials:
      github: hidb4ai
```

For an author with an X account, add `x: actual_handle` under `socials`. Keep role and organization details in `description`.

## Cover images

Pages use a consistent 16:9 cover layout: the top 16% is reserved for branding and the lower 84% shows the complete illustration. The component adds the transparent PowerContext logo without overlapping the artwork. Supply cover artwork without a logo; both languages use this layout in listings and article pages.

Every published post requires a cover and descriptive alternative text. Drafts may omit them. Prefer a 16:9 illustration (recommended 1600 × 900) with no embedded title, relevant to the article. Both languages share the same `cover` path and localize `cover_alt`. Keep the image under `docs/assets/blog/<slug>/`; the build rejects missing files or missing cover metadata before publication.

The cover appears in featured stories, article lists, the article header, and social sharing metadata. Body illustrations remain optional and should explain the content rather than meet a fixed image count. The publishing workflow prepares a suitable cover when the supplied article has none; it does not automatically choose the first body image.

## Body figures and language

Text-free covers and illustrations may be shared. Diagrams and charts with explanatory text require separate localized assets, such as `.zh.svg` and `.en.svg`, referenced by the corresponding Markdown language. Both versions must preserve the same data, flows, colors and layout; keep API identifiers, code and product names unchanged. Use Markdown text for section headings instead of heading images.

When source artwork is explicitly requested, preserve it unchanged in both editions and localize its alternative text and captions. The MiniMax Code article uses the nine original OceanBase figures.

Natural-language prompts, code comments, sample questions and link destinations must match the article language. Preserve executable syntax, API identifiers and product names. Original product screenshots may retain their interface language, but must have localized alternative text and captions identifying them as original screenshots. Review image text as well as Markdown before publishing.

## Lists and author pages

Published posts appear automatically, newest first, with pinned posts first. Lists show ten posts per page. The same pagination applies to author pages and yearly archives. Drafts and future-dated posts are excluded from the public build. Rebuild on the publication date to publish a scheduled article.

- Blog: `/en/blog/` and `/zh/blog/`
- Article: `/<language>/blog/<slug>/`
- Further pages: `/<language>/blog/page/2/`
- Author: `/<language>/blog/authors/<author-id>/`
- Archive: `/<language>/blog/archive/<year>/`

The original site navigation, language switch, theme, and footer are reused. Blog styles are scoped to blog pages. Publishing a post requires no edits to homepage components or navigation lists.

## Preview and validation

From the repository root, run `make docs` to preview the site. After content edits, restart the preview to regenerate Markdown content. Open the HTTP preview URL; source templates are not standalone web pages.

Before delivery, run:

```bash
make docs-test
uv run pytest tests/e2e/test_blog_site.py -q
```

The build validates paired editions, metadata, author references, pagination, static pages, and internal links. Deploy only when explicitly requested.
