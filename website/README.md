# PowerContext website

The bilingual PowerContext website, built with Next.js and Fumadocs.

## Local development

Requires Node.js 22, pnpm 11, and uv.

```bash
cd website
pnpm install
pnpm dev
```

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
- Edit source files. Do not edit generated files in `website/content/docs/`, `website/.generated/`, or `website/out/`.

## Deployment

Manually run the `Deploy website` workflow on the `master` branch of `oceanbase/powercontext`
to publish to [powercontext.oceanbase.io](https://powercontext.oceanbase.io/).
