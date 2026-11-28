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
import type { CSSProperties } from 'react';
import { Avatar, AvatarGroup, buttonClasses } from '@astrafoundry/design/ui';
import { GitHubStarButton } from '@/components/github-star-button';
import { HomeContributors } from '@/components/home-contributors';
import type { HomeContent } from '@/lib/home-content';
import type { Language } from '@/lib/i18n';
import { repositoryUrl } from '@/lib/urls';
import { agentBySlug } from './agents';
import { HeroDemo } from './hero-demo';
import { HeroStage } from './hero-stage';
import { revealScript } from './reveal';

export function normalizeHref(href: string) {
  return `/${href.replace(/^\/+/, '').replace(/\/$/, '')}`;
}

/**
 * The first screen: one left-aligned column of the headline, what
 * PowerContext does in one paragraph, and the install and source links; below
 * it, the stage, a photograph that carries only the product demo and widens
 * to fill the viewport as the reader scrolls. Every real control and line of
 * copy stays outside the art.
 */
/** The agents whose marks stand in the headline, in front of the word for agents. */
const headlineAgents = ['codex', 'claude-code', 'pi'].map(agentBySlug);

/**
 * Codex, Claude Code and Pi as one overlapping row of avatars. The words
 * around them already say "agents", so the row is decorative; each theme
 * shows the marks drawn for it.
 */
function AgentAvatars() {
  return (
    <span aria-hidden="true" className="pc-hero-agents">
      {(['logo', 'darkLogo'] as const).map((variant) => (
        <AvatarGroup
          className="pc-hero-agents-row"
          data-variant={variant}
          key={variant}
          label=""
          style={{ '--avatar-cutout': 'var(--background)' } as CSSProperties}
        >
          {headlineAgents.map((agent) => <Avatar key={agent.slug} name={agent.name} src={agent[variant]} />)}
        </AvatarGroup>
      ))}
    </span>
  );
}

/** A headline line with `{agents}` replaced by the avatar row. */
function withAgents(line: string) {
  const [before, after] = line.split('{agents}');
  if (after === undefined) return line;
  return <>{before}<AgentAvatars />{after}</>;
}

export function HomeHero({ home, lang, contributors, stars }: {
  home: HomeContent;
  lang: Language;
  contributors: Array<{ login: string; avatar: string }>;
  stars: number | null;
}) {
  const { hero } = home;
  const community = hero.community;
  return (
    <section aria-labelledby="pc-hero-title" className="pc-hero">
      <script dangerouslySetInnerHTML={{ __html: revealScript }} />
      <div className="pc-wrap pc-hero-copy">
        <h1 className="pc-display pc-hero-title" id="pc-hero-title">
          {hero.title.map((line) => <span key={line}>{withAgents(line)}</span>)}
        </h1>
        <p className="pc-hero-lead">
          {hero.lead}{' '}
          <Link className="pc-link" href={normalizeHref(hero.learn_more.href)}>{hero.learn_more.label}</Link>
        </p>
        <div className="pc-hero-actions">
          <Link className={buttonClasses('primary', 'lg')} href={normalizeHref(hero.action.href)}>
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
      <HeroStage>
        <HeroDemo hero={hero} />
      </HeroStage>
    </section>
  );
}
