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

import { HomeLayout } from 'fumadocs-ui/layouts/home';
import { HomeHero } from '@/components/home/home-hero';
import { ContinuitySection, EcosystemSection, OnboardingSection } from '@/components/home/home-sections';
import { RevealOnScroll } from '@/components/home/reveal';
import { SiteFooter } from '@/components/site-footer';
import { getHomeContent } from '@/lib/home-content';
import { githubRepository } from '@/lib/github-stars';
import type { Language } from '@/lib/i18n';
import { baseOptions } from '@/lib/site';
import { repositoryUrl } from '@/lib/urls';
import githubStars from '../../.generated/github-stars.json';
import githubContributors from '../../.generated/github-contributors.json';
import './home/home.css';

export async function HomePage({ lang }: { lang: Language }) {
  const home = await getHomeContent(lang);
  const repository = githubRepository(repositoryUrl);
  const contributors: Array<{ login: string; avatar: string }> = githubContributors.repository === repository
    ? githubContributors.contributors : [];

  return (
    <HomeLayout {...baseOptions(lang)} className="pc-home">
      <HomeHero
        contributors={contributors}
        home={home}
        lang={lang}
        stars={githubStars.repository === repository ? githubStars.count : null}
      />
      <OnboardingSection content={home.onboarding} lang={lang} />
      <ContinuitySection content={home.continuity} credit={home.photo_credit} />
      <EcosystemSection content={home.ecosystem} lang={lang} />
      <RevealOnScroll />
      <SiteFooter lang={lang} />
    </HomeLayout>
  );
}
