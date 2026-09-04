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

'use client';

import { ArrowDown } from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState, type ReactNode } from 'react';
import { BrandLogo } from '@/components/brand-logo';
import type { HomeContent } from '@/lib/home-content';
import type { Language } from '@/lib/i18n';

type AgentBase = {
  darkLogo: string;
  logo: string;
  name: string;
};

type Agent = AgentBase & ({ href: string; slug?: never } | { href?: never; slug: string });

const agents: Agent[] = [
  {
    darkLogo:
      'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/codex-color.png?size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/codex-color.png?size=120',
    name: 'Codex',
    slug: 'configure-codex',
  },
  {
    darkLogo:
      'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/claudecode-color.png?size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/claudecode-color.png?size=120',
    name: 'Claude Code',
    slug: 'configure-claude-code',
  },
  {
    darkLogo:
      'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/deepseek-color.png?size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/deepseek-color.png?size=120',
    name: 'DeepSeek Harness',
    slug: 'configure-dsh',
  },
  {
    darkLogo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/hermesagent.png?raw=true&size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/hermesagent.png?raw=true&size=120',
    name: 'Hermes Agent',
    slug: 'configure-hermes',
  },
  {
    darkLogo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/opencode.png?size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/opencode.png?size=120',
    name: 'OpenCode',
    slug: 'configure-opencode',
  },
  {
    darkLogo:
      'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/openclaw-color.png?size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/openclaw-color.png?size=120',
    name: 'OpenClaw',
    slug: 'configure-openclaw',
  },
  {
    darkLogo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/pi.png?size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/pi.png?size=120',
    name: 'Pi Coding Agent',
    slug: 'configure-pi',
  },
  {
    darkLogo: 'https://thesvg.org/icons/workbuddy/default.svg?size=120',
    logo: 'https://thesvg.org/icons/workbuddy/default.svg?size=120',
    name: 'WorkBuddy',
    slug: 'configure-workbuddy',
  },
  {
    darkLogo: 'https://github.com/bubbuild.png?size=120',
    href: 'https://github.com/oceanbase/powercontext/tree/master/integrations/bub',
    logo: 'https://github.com/bubbuild.png?size=120',
    name: 'Bub',
  },
  {
    darkLogo: 'https://thesvg.org/icons/pydantic/default.svg?size=120',
    logo: 'https://thesvg.org/icons/pydantic/default.svg?size=120',
    name: 'Pydantic AI',
    slug: 'configure-pydantic-ai',
  },
  {
    darkLogo:
      'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/langchain-color.png?size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langchain-color.png?size=120',
    name: 'LangChain',
    slug: 'configure-langchain',
  },
  {
    darkLogo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/dark/langgraph.png?size=120',
    logo: 'https://raw.githubusercontent.com/lobehub/lobe-icons/refs/heads/master/packages/static-png/light/langgraph.png?size=120',
    name: 'LangGraph',
    slug: 'configure-langgraph',
  },
];

function randomAgentGroups() {
  const pool = [...agents];
  for (let index = pool.length - 1; index > 0; index -= 1) {
    const target = Math.floor(Math.random() * (index + 1));
    [pool[index], pool[target]] = [pool[target], pool[index]];
  }
  return {
    input: pool.slice(0, 5),
    output: pool.slice(5, 10),
  };
}

function AgentLogo({ agent }: { agent: Agent }) {
  return (
    <span className="block size-10 shrink-0" role="img" aria-label={agent.name}>
      <img
        alt=""
        className="size-full object-contain dark:hidden"
        height="40"
        loading="lazy"
        src={agent.logo}
        width="40"
      />
      <img
        alt=""
        className="hidden size-full object-contain dark:block"
        height="40"
        loading="lazy"
        src={agent.darkLogo}
        width="40"
      />
    </span>
  );
}

function AgentLink({
  agent,
  ariaLabel,
  children,
  className,
  lang,
}: {
  agent: Agent;
  ariaLabel: string;
  children: ReactNode;
  className?: string;
  lang: Language;
}) {
  const external = Boolean(agent.href);
  const href = agent.href ?? `/${lang}/docs/how-to/${agent.slug}`;

  return (
    <Link
      aria-label={ariaLabel}
      className={className}
      href={href}
      rel={external ? 'noreferrer' : undefined}
      target={external ? '_blank' : undefined}
      title={agent.name}
    >
      {children}
    </Link>
  );
}

export function AgentArtifactFlow({ content, lang }: { content: HomeContent['ecosystem']; lang: Language }) {
  const [selectedAgents, setSelectedAgents] = useState(() => ({
    input: agents.slice(0, 5),
    output: agents.slice(5, 10),
  }));

  useEffect(() => setSelectedAgents(randomAgentGroups()), []);

  return (
    <figure aria-label={content.visual_label} className="grid gap-4">
      <div>
        <p className="mb-3 text-sm font-medium">{content.agents_label}</p>
        <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
          {selectedAgents.input.map((agent) => (
            <AgentLink
              agent={agent}
              ariaLabel={`${content.docs_label}: ${agent.name}`}
              className="flex items-center justify-center bg-fd-muted/50 p-4 hover:bg-fd-accent"
              key={agent.name}
              lang={lang}
            >
              <AgentLogo agent={agent} />
            </AgentLink>
          ))}
          <Link
            aria-label={content.all_agents_label}
            className="flex min-h-18 items-center justify-center bg-fd-muted/50 text-2xl text-fd-muted-foreground hover:bg-fd-accent hover:text-fd-foreground"
            href={`/${lang}/docs/reference/integration-capabilities`}
            title={content.all_agents_label}
          >
            <span aria-hidden="true">…</span>
          </Link>
        </div>
      </div>

      <ArrowDown aria-hidden="true" className="mx-auto size-5 text-fd-muted-foreground" />

      <div className="bg-fd-primary/8 p-5 text-center">
        <BrandLogo className="mx-auto w-44" />
        <p className="mt-3 text-sm text-fd-muted-foreground">{content.runtime_label}</p>
      </div>

      <ArrowDown aria-hidden="true" className="mx-auto size-5 text-fd-muted-foreground" />

      <div>
        <p className="mb-3 text-sm font-medium">{content.artifacts_label}</p>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {content.artifacts.map((artifact) => (
            <div className="bg-fd-muted/50 p-4" key={artifact.name}>
              <strong className="text-sm font-medium">{artifact.name}</strong>
              <p className="mt-1 text-xs text-fd-muted-foreground">{artifact.description}</p>
            </div>
          ))}
        </div>
      </div>

      <ArrowDown aria-hidden="true" className="mx-auto size-5 text-fd-muted-foreground" />

      <div className="flex flex-col gap-4 bg-fd-primary/8 p-5 sm:flex-row sm:items-center sm:justify-between">
        <p className="font-medium">{content.output_label}</p>
        <div className="ml-auto flex flex-wrap items-center justify-end gap-3">
          {selectedAgents.output.map((agent) => (
            <AgentLink
              agent={agent}
              ariaLabel={`${content.docs_label}: ${agent.name}`}
              key={agent.name}
              lang={lang}
            >
              <AgentLogo agent={agent} />
            </AgentLink>
          ))}
        </div>
      </div>
    </figure>
  );
}
