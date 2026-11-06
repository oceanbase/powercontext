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

import { createMDX } from 'fumadocs-mdx/next';

const withMDX = createMDX();

/** @type {import('next').NextConfig} */
const config = {
  basePath: process.env.NEXT_PUBLIC_BASE_PATH?.replace(/\/$/, '') || '',
  // Next defaults to (visible CPUs - 1) build workers, so peak memory grows with the
  // host's CPU count. Cap it: memory-constrained build runners were OOM-killed (exit 137)
  // while collecting page data. Every page is still rendered.
  experimental: { cpus: 2 },
  images: { unoptimized: true },
  output: 'export',
  reactStrictMode: true,
  serverExternalPackages: ['@takumi-rs/core'],
  trailingSlash: true,
};

export default withMDX(config);
