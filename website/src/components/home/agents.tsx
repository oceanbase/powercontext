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

import { withBasePath } from '@/lib/urls';

export type AgentStatus = 'official' | 'community' | 'evaluation';

export type Agent = {
  darkLogo: string;
  /** How the host or framework connects, in the integration guides' own terms. */
  integration: string;
  logo: string;
  name: string;
  /** Integration guide under `/{lang}/docs/integrations/`. */
  slug: string;
  status: AgentStatus;
};

const icon = (name: string) => ({
  logo: withBasePath(`/icons/${name}.png`),
  darkLogo: withBasePath(`/icons/${name}-dark.png`),
});

/** Supported agents, in the order the home page lists them. */
export const agents: Agent[] = [
  { ...icon('codex'), name: 'Codex', slug: 'codex', integration: 'Prompt Hook + MCP + Skill', status: 'official' },
  { ...icon('claude-code'), name: 'Claude Code', slug: 'claude-code', integration: 'Prompt Hook + MCP + Skill', status: 'community' },
  { ...icon('deepseek'), name: 'DeepSeek Harness', slug: 'dsh', integration: 'HTTP plugin + pc_* + /pc', status: 'community' },
  { ...icon('hermes'), name: 'Hermes Agent', slug: 'hermes', integration: 'MemoryProvider + /pc', status: 'community' },
  { ...icon('opencode'), name: 'OpenCode', slug: 'opencode', integration: 'HTTP plugin + pc_*', status: 'community' },
  { ...icon('openclaw'), name: 'OpenClaw', slug: 'openclaw', integration: 'Memory plugin + lifecycle hooks', status: 'community' },
  { ...icon('pi'), name: 'Pi Coding Agent', slug: 'pi', integration: 'Extension + pc_* + /pc', status: 'community' },
  {
    darkLogo: 'https://thesvg.org/icons/workbuddy/default.svg?size=120',
    logo: 'https://thesvg.org/icons/workbuddy/default.svg?size=120',
    name: 'WorkBuddy',
    slug: 'workbuddy',
    integration: 'Prompt Hook + MCP + Skill',
    status: 'community',
  },
  {
    darkLogo: 'https://github.com/bubbuild.png?size=120',
    logo: 'https://github.com/bubbuild.png?size=120',
    name: 'Bub',
    slug: 'evaluation',
    integration: 'Python Client plugin',
    status: 'evaluation',
  },
  {
    darkLogo: 'https://thesvg.org/icons/pydantic/default.svg?size=120',
    logo: 'https://thesvg.org/icons/pydantic/default.svg?size=120',
    name: 'Pydantic AI',
    slug: 'pydantic-ai',
    integration: 'Toolset',
    status: 'community',
  },
  { ...icon('langchain'), name: 'LangChain', slug: 'langchain', integration: 'Middleware', status: 'community' },
  { ...icon('langgraph'), name: 'LangGraph', slug: 'langgraph', integration: 'Recall hook + Memory tools', status: 'community' },
];

export function agentBySlug(slug: string): Agent {
  const agent = agents.find((candidate) => candidate.slug === slug);
  if (!agent) throw new Error(`Unknown agent: ${slug}`);
  return agent;
}

/** An agent's mark, swapping to its dark variant under the dark theme. Decorative. */
export function AgentIcon({ agent, size, lazy = false }: { agent: Agent; size: number; lazy?: boolean }) {
  const loading = lazy ? 'lazy' : undefined;
  return (
    <span aria-hidden="true" className="pc-agent-icon">
      <img alt="" height={size} loading={loading} src={agent.logo} width={size} />
      <img alt="" height={size} loading={loading} src={agent.darkLogo} width={size} />
    </span>
  );
}
