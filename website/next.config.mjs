import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  output: 'export',
  reactStrictMode: true,
  serverExternalPackages: ['@takumi-rs/core'],
  trailingSlash: true,
};

export default withMDX(config);
