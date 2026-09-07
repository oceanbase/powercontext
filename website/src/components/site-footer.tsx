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

import Link from 'next/link';
import { BrandLogo } from './brand-logo';
import type { Language } from '@/lib/i18n';

const copy = {
  en: {
    description: 'Project context that carries knowledge and active work across sessions.',
    product: 'Product',
    resources: 'Resources',
    docs: 'Docs',
    benchmarks: 'Benchmarks',
    changelog: 'Changelog',
    python: 'Python API',
    http: 'HTTP API',
    maintained: 'Maintained by',
  },
  zh: {
    description: '让项目知识和当前任务跨会话延续的上下文运行层。',
    product: '产品',
    resources: '资源',
    docs: '文档',
    benchmarks: '基准测试',
    changelog: '更新日志',
    python: 'Python API',
    http: 'HTTP API',
    maintained: '维护方',
  },
} as const;

export function SiteFooter({ lang }: { lang: Language }) {
  const text = copy[lang];

  return (
    <footer className="mt-20 border-t bg-fd-card/40">
      <div className="mx-auto grid w-full max-w-(--fd-layout-width) gap-10 px-4 py-12 md:grid-cols-4">
        <div className="md:col-span-2">
          <Link aria-label="PowerContext" href={`/${lang}`}>
            <BrandLogo className="w-40" />
          </Link>
          <p className="mt-4 max-w-sm text-sm leading-6 text-fd-muted-foreground">{text.description}</p>
          <p className="mt-5 text-xs text-fd-muted-foreground">
            {text.maintained}{' '}
            <a className="text-fd-foreground hover:underline" href="https://oceanbase.com" rel="noreferrer" target="_blank">
              OceanBase
            </a>
          </p>
        </div>
        <nav aria-label={text.product}>
          <p className="text-sm font-medium">{text.product}</p>
          <ul className="mt-4 space-y-3 text-sm text-fd-muted-foreground">
            <li><Link className="hover:text-fd-foreground" href={`/${lang}/docs`}>{text.docs}</Link></li>
            <li><Link className="hover:text-fd-foreground" href={`/${lang}/benchmarks`}>{text.benchmarks}</Link></li>
            <li><Link className="hover:text-fd-foreground" href={`/${lang}/changelog`}>{text.changelog}</Link></li>
          </ul>
        </nav>
        <nav aria-label={text.resources}>
          <p className="text-sm font-medium">{text.resources}</p>
          <ul className="mt-4 space-y-3 text-sm text-fd-muted-foreground">
            <li><Link className="hover:text-fd-foreground" href={`/${lang}/modules`}>{text.python}</Link></li>
            <li><Link className="hover:text-fd-foreground" href="/api">{text.http}</Link></li>
            <li><a className="hover:text-fd-foreground" href="https://github.com/oceanbase/powercontext" rel="noreferrer" target="_blank">GitHub</a></li>
          </ul>
        </nav>
      </div>
    </footer>
  );
}
