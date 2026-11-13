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

import { ArrowRight, FileText } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import { BrandLogo } from '@/components/brand-logo';
import type { HomeContent } from '@/lib/home-content';
import type { Language } from '@/lib/i18n';
import { AgentIcon, agents, type Agent } from './agents';
import { Backdrop } from './backdrop';
import { CopyCommand } from './copy-command';
import { MockWindow } from './session-windows';

function SectionHead({ id, title, lead, children }: {
  id: string;
  title: string | string[];
  lead: string;
  children?: ReactNode;
}) {
  return (
    <header className="pc-section-head" data-reveal="">
      <div>
        <h2 className="pc-display pc-section-title" id={id}>
          {Array.isArray(title) ? title.map((line) => <span key={line}>{line}</span>) : title}
        </h2>
        <p className="pc-section-lead">{lead}</p>
      </div>
      {children}
    </header>
  );
}

export function OnboardingSection({ content, lang }: { content: HomeContent['onboarding']; lang: Language }) {
  return (
    <section aria-labelledby="pc-start-title" className="pc-section">
      <SectionHead id="pc-start-title" lead={content.lead} title={content.title}>
        <Link className="pc-button pc-button-quiet" href={`/${lang}/docs/get-started/quickstart`}>
          {content.guide_label} <ArrowRight aria-hidden="true" strokeWidth={2} />
        </Link>
      </SectionHead>
      <ol className="pc-grid pc-steps" data-reveal="">
        {content.steps.map((step, index) => (
          <li className="pc-step" key={step.title}>
            <div className="pc-step-body">
              <h3><span className="pc-step-index">{index + 1}</span>{step.title}</h3>
              <p>{step.description}</p>
              {step.command ? (
                <CopyCommand command={step.command} copiedLabel={content.copied_label} copyLabel={content.copy_label} />
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** The Dashboard's Memory view, one entry open. A picture of the product: inert and labelled. */
function DashboardWindow({ dashboard, label }: { dashboard: HomeContent['continuity']['dashboard']; label: string }) {
  const [open] = dashboard.entries;
  return (
    <div aria-label={label} className="pc-dashboard" role="img">
      <div inert>
        <MockWindow
          title={dashboard.title}
          trailing={<span className="pc-dashboard-scope">{dashboard.scope}</span>}
        >
          <div className="pc-window-body pc-dashboard-panes">
            <div className="pc-dashboard-list">
              <div className="pc-dashboard-head">
                <span>{dashboard.list_label}</span>
                <span>{dashboard.revision_label}</span>
                <span>{dashboard.state_label}</span>
              </div>
              {dashboard.entries.map((entry) => (
                <div
                  className="pc-dashboard-row"
                  data-open={entry.id === open.id ? '' : undefined}
                  data-state={entry.state}
                  key={entry.id}
                >
                  <span>{entry.title}</span>
                  <span className="pc-rev">@{entry.revision}</span>
                  <span className="pc-state" data-state={entry.state}>{dashboard.states[entry.state]}</span>
                </div>
              ))}
            </div>
            <div className="pc-dashboard-detail">
              <p className="pc-dashboard-ref">atomic-memory/{open.id}@{open.revision}</p>
              <h3>{open.title}</h3>
              <p className="pc-dashboard-meta">
                <span><b>{dashboard.revision_label}</b><span className="pc-rev">{open.revision}</span> · {dashboard.current_label}</span>
                <span><b>{dashboard.state_label}</b><span className="pc-state" data-state={open.state}>{dashboard.states[open.state]}</span></span>
              </p>
              <p>{dashboard.body}</p>
              <div className="pc-dashboard-sources">
                <h4>{dashboard.sources_label}</h4>
                <ul>
                  {dashboard.sources.map((source) => (
                    <li key={source}><FileText aria-hidden="true" strokeWidth={1.75} /><span>{source}</span></li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </MockWindow>
      </div>
    </div>
  );
}

export function ContinuitySection({ content }: { content: HomeContent['continuity'] }) {
  const [current, runtime, next] = content.steps;
  return (
    <section aria-labelledby="pc-continuity-title" className="pc-section pc-continuity">
      <SectionHead id="pc-continuity-title" lead={content.lead} title={content.title} />
      <div data-reveal="">
        <div className="pc-stage">
          <Backdrop id="lupine" />
          <DashboardWindow dashboard={content.dashboard} label={content.visual_label} />
        </div>
      </div>
      <div className="pc-grid pc-flow" data-reveal="">
        {[current, runtime, next].map((step, index) => (
          <section className={index === 1 ? 'pc-flow-runtime' : undefined} key={step.title}>
            <h3>
              {index > 0 ? <ArrowRight aria-hidden="true" strokeWidth={1.75} /> : null}
              {step.title}
            </h3>
            <ul>
              {step.items.map((item) => <li key={item}>{item}</li>)}
            </ul>
          </section>
        ))}
      </div>
    </section>
  );
}

function AgentLink({ agent, lang, status, docsLabel }: {
  agent: Agent;
  lang: Language;
  status: string;
  docsLabel: string;
}) {
  return (
    <Link
      aria-label={`${docsLabel}: ${agent.name} · ${status}`}
      className="pc-agent"
      href={`/${lang}/docs/integrations/${agent.slug}`}
    >
      <AgentIcon agent={agent} lazy size={32} />
      <span>
        <strong>{agent.name}</strong>
        <small>{status}</small>
      </span>
    </Link>
  );
}

export function EcosystemSection({ content, lang }: { content: HomeContent['ecosystem']; lang: Language }) {
  const half = Math.ceil(agents.length / 2);
  const agentRow = (group: Agent[], withAll: boolean) => (
    <div className="pc-agents">
      {group.map((agent) => (
        <AgentLink
          agent={agent}
          docsLabel={content.docs_label}
          key={agent.slug}
          lang={lang}
          status={content.status[agent.status]}
        />
      ))}
      {withAll ? (
        <Link className="pc-agent pc-agent-all" href={`/${lang}/docs/integrations`}>
          <span aria-hidden="true"><ArrowRight strokeWidth={1.75} /></span>
          <span>{content.all_agents_label}</span>
        </Link>
      ) : null}
    </div>
  );

  return (
    <section aria-labelledby="pc-ecosystem-title" className="pc-section">
      <SectionHead id="pc-ecosystem-title" lead={content.lead} title={content.title} />
      <figure aria-label={content.visual_label} className="pc-grid" data-reveal="">
        <div className="pc-ledger-row">
          <p className="pc-ledger-label">{content.agents_label}</p>
          {agentRow(agents.slice(0, half), false)}
        </div>
        <div className="pc-ledger-row">
          <p className="pc-ledger-label">{content.runtime_label}</p>
          <div className="pc-runtime"><BrandLogo className="w-40" /></div>
        </div>
        <div className="pc-ledger-row">
          <p className="pc-ledger-label">{content.artifacts_label}</p>
          <dl className="pc-artifacts">
            {content.artifacts.map((artifact) => (
              <div key={artifact.name}>
                <dt className="pc-display">{artifact.name}</dt>
                <dd>{artifact.description}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="pc-ledger-row">
          <p className="pc-ledger-label">{content.output_label}</p>
          {agentRow(agents.slice(half), true)}
        </div>
      </figure>
    </section>
  );
}
