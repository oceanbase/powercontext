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

import { ArrowRight } from 'lucide-react';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { DocsDescription, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { AgentArtifactFlow } from '@/components/agent-artifact-flow';
import { ContextFlow } from '@/components/context-flow';
import { SiteFooter } from '@/components/site-footer';
import { SpiralVisual } from '@/components/spiral-visual';
import { baseOptions } from '@/lib/site';
import { getHomeContent } from '@/lib/home-content';
import { isLanguage } from '@/lib/i18n';

function normalizeHref(href: string) {
  return `/${href.replace(/^\/+/, '').replace(/\/$/, '')}`;
}

export default async function HomePage({ params }: { params: Promise<{ lang: string }> }) {
  const { lang } = await params;
  if (!isLanguage(lang)) notFound();
  const home = await getHomeContent(lang);

  return (
    <HomeLayout {...baseOptions(lang)}>
      <main>
        <section className="pc-kv">
          <div className="mx-auto grid w-full max-w-(--fd-layout-width) items-center gap-12 px-4 py-16 lg:grid-cols-2 lg:py-20">
            <header className="grid gap-4">
              <p className="text-fd-primary">{home.hero.label}</p>
              <DocsTitle>
                {home.hero.title.map((line) => <span className="block" key={line}>{line}</span>)}
              </DocsTitle>
              <DocsDescription className="mb-0">{home.hero.lead}</DocsDescription>
              <div className="flex flex-wrap gap-3">
                {home.hero.actions.map((action) => (
                  <Link
                    className={buttonVariants({ variant: action.kind === 'primary' ? 'primary' : 'outline' })}
                    href={normalizeHref(action.href)}
                    key={action.href}
                  >
                    {action.label} {action.kind === 'primary' ? <ArrowRight aria-hidden="true" className="size-4" /> : null}
                  </Link>
                ))}
              </div>
            </header>
            <div className="mx-auto hidden w-full max-w-lg lg:block">
              <SpiralVisual />
            </div>
          </div>
        </section>
        <div className="mx-auto w-full max-w-(--fd-layout-width) px-4 py-20 md:py-28">
          <section className="grid items-start gap-10 lg:grid-cols-3">
            <div className="prose max-w-xl">
              <h2>{home.continuity.title}</h2>
              <p>{home.continuity.lead}</p>
            </div>
            <div className="lg:col-span-2">
              <ContextFlow label={home.continuity.visual_label} steps={home.continuity.steps} />
            </div>
          </section>
          <section className="mt-28 grid gap-10 lg:grid-cols-3">
            <div className="prose">
              <h2>
                {home.ecosystem.title.map((line) => <span className="block" key={line}>{line}</span>)}
              </h2>
              <p>{home.ecosystem.lead}</p>
            </div>
            <div className="lg:col-span-2">
              <AgentArtifactFlow content={home.ecosystem} lang={lang} />
            </div>
          </section>
        </div>
      </main>
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}
