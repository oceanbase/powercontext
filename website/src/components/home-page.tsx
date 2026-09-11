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
import { buttonVariants } from 'fumadocs-ui/components/ui/button';
import { DocsDescription, DocsTitle } from 'fumadocs-ui/layouts/docs/page';
import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { AgentArtifactFlow } from '@/components/agent-artifact-flow';
import { ContextFlow } from '@/components/context-flow';
import { SiteFooter } from '@/components/site-footer';
import { SpiralVisual } from '@/components/spiral-visual';
import { getHomeContent } from '@/lib/home-content';
import type { Language } from '@/lib/i18n';
import { baseOptions } from '@/lib/site';
import { repositoryUrl } from '@/lib/urls';

function normalizeHref(href: string) {
  return `/${href.replace(/^\/+/, '').replace(/\/$/, '')}`;
}

export async function HomePage({ lang }: { lang: Language }) {
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
        <section className="border-y bg-fd-card/50" aria-labelledby="start-title">
          <div className="mx-auto w-full max-w-(--fd-layout-width) px-4 py-12 md:py-16">
            <div className="mb-8 flex flex-wrap items-end justify-between gap-6">
              <div className="max-w-2xl">
                <h2 id="start-title" className="text-2xl font-semibold tracking-tight">{home.onboarding.title}</h2>
                <p className="mt-3 leading-7 text-fd-muted-foreground">{home.onboarding.lead}</p>
              </div>
              <Link className={buttonVariants({ variant: 'primary' })} href={`/${lang}/docs/get-started/quickstart`}>
                {home.onboarding.guide_label} <ArrowRight aria-hidden="true" className="size-4" />
              </Link>
            </div>
            <ol className="grid gap-4 md:grid-cols-2">
              {home.onboarding.steps.map((step, index) => (
                <li key={step.title} className="min-w-0 rounded-lg border bg-fd-background p-5">
                  <h3 className="flex items-center gap-3 font-medium">
                    <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-fd-primary/10 text-sm text-fd-primary">
                      {index + 1}
                    </span>
                    {step.title}
                  </h3>
                  <p className="mt-3 text-sm leading-6 text-fd-muted-foreground">{step.description}</p>
                  {step.command ? (
                    <pre className="mt-4 overflow-x-auto rounded bg-fd-muted p-3 text-xs leading-6"><code>{step.command}</code></pre>
                  ) : null}
                </li>
              ))}
            </ol>
            <p className="mt-6 text-sm leading-6 text-fd-muted-foreground">
              <span className="font-medium text-fd-foreground">{home.onboarding.preview_label}</span>{' '}
              {home.onboarding.preview_note}{' '}
              <a href={repositoryUrl} className="text-fd-primary underline underline-offset-4" rel="noreferrer" target="_blank">
                {home.onboarding.repository_label}
              </a>
            </p>
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
