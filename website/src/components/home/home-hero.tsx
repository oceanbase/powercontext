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
import { GitHubStarButton } from '@/components/github-star-button';
import { HomeContributors } from '@/components/home-contributors';
import type { HomeContent } from '@/lib/home-content';
import type { Language } from '@/lib/i18n';
import { repositoryUrl } from '@/lib/urls';
import { Backdrop } from './backdrop';
import { HeroScene } from './hero-scene';
import { introScript, PART_ATTR } from './intro';

const part = (name: 'card' | 'title' | 'lead') => ({ [PART_ATTR]: name });

export function normalizeHref(href: string) {
  return `/${href.replace(/^\/+/, '').replace(/\/$/, '')}`;
}

/**
 * The first screen: one stage card on the iris photograph. The copy sits
 * bottom-left on the ink scrim; the handoff figure and its two sessions sit
 * on the right (under the copy on narrow screens). The server renders the
 * final card; the inline script decides before paint whether the cursors
 * finish it.
 */
export function HomeHero({ home, lang, contributors, stars }: {
  home: HomeContent;
  lang: Language;
  contributors: Array<{ login: string; avatar: string }>;
  stars: number | null;
}) {
  const { hero } = home;
  const community = hero.community;
  const { label: _heroLabel, ...sceneHero } = hero;
  return (
    <section aria-labelledby="pc-hero-title" className="pc-hero">
      <script dangerouslySetInnerHTML={{ __html: introScript }} />
      <div className="pc-frame">
        <div className="pc-stage" {...part('card')}>
          <Backdrop id="iris" priority />
          <div aria-hidden="true" className="pc-hero-scrim" />
          <div className="pc-hero-body">
            <div className="pc-hero-copy">
              <h1 className="pc-display pc-hero-title" id="pc-hero-title" {...part('title')}>
                {hero.title.map((line) => <span key={line}>{line}</span>)}
              </h1>
              <div {...part('lead')}>
                <p className="pc-hero-lead">
                  {hero.lead}{' '}
                  <Link href={normalizeHref(hero.learn_more.href)}>{hero.learn_more.label}</Link>
                </p>
                <div className="pc-hero-actions">
                  <Link className="pc-button pc-button-primary" href={normalizeHref(hero.action.href)}>
                    {hero.action.label} <ArrowRight aria-hidden="true" strokeWidth={2} />
                  </Link>
                  <GitHubStarButton
                    initialCount={stars}
                    label={community.star_label}
                    lang={lang}
                    url={repositoryUrl}
                  />
                </div>
                <div className="pc-hero-community">
                  <div>
                    <HomeContributors
                      contributors={contributors}
                      label={community.contributors_label}
                      url={`${repositoryUrl}/graphs/contributors`}
                    />
                    <a href={`${repositoryUrl}/blob/master/CONTRIBUTING.md`} rel="noopener noreferrer" target="_blank">
                      {community.contribute_label}
                    </a>
                  </div>
                  <a href={`${repositoryUrl}/blob/master/LICENSE`} rel="noopener noreferrer" target="_blank">
                    {community.license_label}
                  </a>
                </div>
              </div>
            </div>
            <HeroScene hero={sceneHero} />
          </div>
        </div>
      </div>
    </section>
  );
}
