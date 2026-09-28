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

export const basePath = process.env.NEXT_PUBLIC_BASE_PATH?.replace(/\/$/, '') || '';
export const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || 'https://powercontext.oceanbase.io';
export const repositoryUrl = process.env.NEXT_PUBLIC_REPOSITORY_URL || 'https://github.com/oceanbase/powercontext';

// Next Link and router already apply basePath; use this only for raw URLs and assets.
export function withBasePath(pathname: string) {
  return `${basePath}${pathname.startsWith('/') ? pathname : `/${pathname}`}`;
}

export function absoluteSiteUrl(pathname: string) {
  return new URL(withBasePath(pathname), siteUrl).href;
}
