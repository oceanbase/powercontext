---
title: Maintain website metadata and Markdown exports
description: Update search snippets, sharing previews, and content discovery when changing website content or routes.
---

# Maintain website metadata and Markdown exports

Use this guide when editing website content, adding a page, or changing the deployment URL. The Fumadocs site builds HTML, metadata, and Markdown files for GitHub Pages. The deployed site serves those files without a Next.js server.

## Update a page title or description

Edit the source Markdown under `docs/en/` and `docs/zh/`. Product documentation, development guides, and RFCs use their own front matter. For blog articles, follow the [blog publishing guide](https://github.com/oceanbase/powercontext/blob/master/docs/en/blog/publishing.md) and update both editions together. The build copies these sources into `website/content/`; make changes in the original files.

Write the description for someone deciding whether to open the page:

| Page type | Describe |
| --- | --- |
| Tutorial | What the reader will accomplish by following the lesson |
| How-to guide or migration guide | The task, any prerequisite that affects its use, and the expected result |
| Reference | The interfaces, fields, or rules the reader can look up |
| Explanation | The question the page answers and the design decisions it explains |
| RFC | The proposed design and its scope |
| Blog article | The problem or argument developed in the article |

Use terms that appear in the page. Keep prerequisites that affect safety or compatibility, and distinguish a proposal from a released capability. Search snippets need a useful summary of the page rather than a list of product keywords.

For standalone pages, update the title and description passed to `pageMetadata` in the page's route file. Site descriptions live in `website/src/lib/metadata.ts`. Blog article metadata uses the article summary; author pages describe the author's posts, and archive pages identify the publication year. Paginated titles include the page number.

## Add a route to search and Markdown discovery

Call `pageMetadata` with the page's path and language. Supply language alternates only for existing counterpart pages. Documentation routes use `contentMetadata` to find those counterparts. The English home has canonical URL `/`, including when reached through `/en/`; other canonical page URLs end in `/`.

Register standalone pages in `website/src/app/sitemap.ts`. Pages from the documentation, blog, Python, and HTTP API sources enter the sitemap through their loaders. The sitemap excludes the duplicate English home, legacy redirects, and unpublished blog articles. Set `lastmod` only when the record has a publication or update date. A rebuild does not establish when the content changed. `website/src/app/robots.ts` declares the sitemap URL and allows crawling.

Documentation and published blog articles also enter the Markdown exports. Their HTML metadata links to the Markdown alternative, and their page actions let readers copy or open it. Python and HTTP API references have HTML pages and sitemap entries; they are not part of the Markdown collection.

## Change a sharing preview or structured data

Edit `website/src/app/og.png/route.tsx` to change the default sharing image. It uses the existing brand logo and renders a 1200 by 630 PNG during the build. Keep its text short enough to read in a link preview. Page-specific images go through `pageMetadata`; published blog articles use their cover images. The site icon is `website/public/icon.svg`.

Use the same article facts in the visible page and its `BlogPosting` JSON-LD: title, summary, author profile URLs, cover image, publication date, and update date. Documentation and blog articles also include breadcrumb JSON-LD. Fumadocs search-index `structuredData` describes headings and content for site search; it does not replace JSON-LD.

## Build with the deployment URL

Set `NEXT_PUBLIC_SITE_URL` to the site's origin and `NEXT_PUBLIC_BASE_PATH` to its deployment subpath, or leave the base path empty for a root deployment. Generated absolute URLs use both values. When serving from a subpath, configure the host to expose `robots.txt` at the origin root so crawlers can find the sitemap.

From the repository root, build and check types:

```sh
make docs-build
pnpm --dir website types:check
```

The build checks local links and static exports. Inspect the output in `website/out/`:

| Output | What to check |
| --- | --- |
| Page HTML | Title, description, canonical URL, existing language counterparts, sharing metadata, and JSON-LD |
| `sitemap.xml` and `robots.txt` | The deployment URL and the set of public pages |
| `og.png` and `icon.svg` | The image renders and text fits |
| `llms.txt` and `llms-full.txt` | The default English index and full text |
| `en/llms.txt`, `zh/llms.txt`, and their `llms-full.txt` counterparts | Each index and full text use the corresponding language |
| `{lang}/llms.mdx/{section}/{slug}/content.md` | The page content and a link to its HTML source |

Markdown sections are `docs`, `development`, `rfcs`, and `blog`. Overview pages omit `{slug}`. `website/src/lib/llms.ts` combines documentation and published blog sources with the Fumadocs loader. The official `llms()` API generates the index from the page tree and renders individual pages and full text from `getText('processed')`. The URL helper and `content.md` route follow the [Fumadocs LLM integration](https://www.fumadocs.dev/docs/integrations/llms).

The static host serves the exported Markdown files directly. Next.js rewrites and `Accept` header negotiation require a server and are not enabled. Relative document links are resolved through the Fumadocs content source and exported as absolute Markdown URLs, preserving queries and fragments. Root-relative links and images use the deployment URL. Code examples and source Markdown files remain unchanged. An existing leading title is removed before adding the page title and source URL. MDX components retain their JSX syntax, following Fumadocs defaults; the export does not flatten them into a separate text format.

Write source text as literal UTF-8. The route handlers declare UTF-8 content types; the static host must also serve `.txt` and `.md` files with a UTF-8 charset. Inspect response headers in the preview or deployment environment when investigating garbled text. These exports help tools read the documentation; search engines decide whether to index or cite it.

Use the publishing environment variables for the build you inspect. Deploy the resulting files through the website deployment workflow when they are ready to publish.
