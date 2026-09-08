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

import type { ComponentProps, FC } from 'react';
import defaultMdxComponents, { createRelativeLink } from 'fumadocs-ui/mdx';
import type { MDXComponents } from 'mdx/types';
import { source } from '@/lib/source';

type SourcePage = ReturnType<typeof source.getPages>[number];

function normalizeRelativeMarkdownHref(href: string) {
  const pathname = href.split(/[?#]/, 1)[0];
  if (
    !/\.mdx?$/.test(pathname)
    || pathname.startsWith('/')
    || /^[a-z][a-z\d+.-]*:/i.test(pathname)
    || pathname.startsWith('.')
  ) {
    return href;
  }

  return `./${href}`;
}

export function createPageLink(page: SourcePage): FC<ComponentProps<'a'>> {
  const localePrefix = page.locale ? `${page.locale}/` : '';
  const localizedPage = {
    ...page,
    path: localePrefix && page.path.startsWith(localePrefix) ? page.path.slice(localePrefix.length) : page.path,
  };
  const RelativeLink = createRelativeLink(source, localizedPage);

  return function PageLink({ href, ...props }) {
    return <RelativeLink href={href ? normalizeRelativeMarkdownHref(href) : href} {...props} />;
  };
}

export function getMDXComponents(components?: MDXComponents) {
  return {
    ...defaultMdxComponents,
    ...components,
  } satisfies MDXComponents;
}

export const useMDXComponents = getMDXComponents;

declare global {
  type MDXProvidedComponents = ReturnType<typeof getMDXComponents>;
}
