import { defineConfig } from 'fumadocs-mdx/config';

export default defineConfig({
  mdxOptions: {
    // Keep public image URLs usable in both Next.js and the Node link validator.
    remarkImageOptions: { useImport: false },
  },
});
