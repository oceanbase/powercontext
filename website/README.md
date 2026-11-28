# PowerContext website

The bilingual PowerContext website, built with Next.js and Fumadocs.

## Local development

Requires Node.js 22, pnpm 11, and uv.

```bash
cd website
pnpm install
pnpm dev
```

## Design

The site uses Sunbeam from [`@astrafoundry/design`](https://www.npmjs.com/package/@astrafoundry/design):
its tokens, Tailwind theme, flat and raised materials, base styles and components such as
`Button`, `Segmented` and `SiteFooterShell`. `src/app/sunbeam.css` imports the package and
holds only PowerContext's changes, chiefly PowerContext blue in the single-accent slot.
Fumadocs chrome is mapped onto the same tokens in `src/app/chrome.css`. Every page shares one
top bar and `src/components/site-footer.tsx`; the docs, HTTP API and Python API sections sit
under that bar through `src/components/docs-shell.tsx`. Blog and changelog pages share
`src/components/editorial.tsx`; diagrams in posts are static HTML drawn with the figure kit in
`src/components/figures.module.css` (see the `blog-figures` skill). Components use semantic
tokens only, so light and dark differ in the token layer alone.

## Validation

Run from `website/`:

```bash
pnpm lint
pnpm test
pnpm build
```

Static output is written to `website/out/`.

## Content sources

- Bilingual content: `docs/en/` and `docs/zh/`.
- API references: generated from `openapi/powercontext.yaml` and `src/powercontext/`.
- Edit source files. Do not edit generated files in `website/.generated/` or `website/out/`.

## Deployment

Manually run the `Deploy website` workflow on the `master` branch of `oceanbase/powercontext`
to publish to [powercontext.oceanbase.io](https://powercontext.oceanbase.io/).
