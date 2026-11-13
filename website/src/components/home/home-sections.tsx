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

import { ArrowRight, ArrowUpRight, Check, FileText } from 'lucide-react';
import Link from 'next/link';
import type { ReactNode } from 'react';
import type { HomeContent } from '@/lib/home-content';
import type { Language } from '@/lib/i18n';
import { AgentIcon, agentBySlug, agents } from './agents';
import { Backdrop } from './backdrop';
import { CopyCommand } from './copy-command';
import { normalizeHref } from './home-hero';
import { MockWindow } from './session-windows';

/** The command the ecosystem section offers; `powercontext setup` asks which hosts to connect. */
const SETUP_COMMAND = 'powercontext setup';

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

/* Features: five cards that are windows onto one photograph ---------------- */

type Features = HomeContent['features'];

function Chip({ children }: { children: ReactNode }) {
  return <span className="pc-chip">{children}</span>;
}

function Fields({ fields }: { fields: Array<{ label: string; value: string }> }) {
  return (
    <dl className="pc-fields">
      {fields.map((field) => (
        <div key={field.label}>
          <dt>{field.label}</dt>
          <dd>{field.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function MemoryMock({ memory, scope }: { memory: Features['memory']; scope: string }) {
  const [open] = memory.entries;
  return (
    <MockWindow className="pc-mock-memory" title={memory.window} trailing={<Chip>{scope}</Chip>}>
      <div className="pc-memory">
        <div className="pc-memory-list">
          {memory.entries.map((entry) => (
            <div
              className="pc-memory-row"
              data-open={entry.id === open.id ? '' : undefined}
              data-state={entry.state}
              key={entry.id}
            >
              <span>{entry.title}</span>
              <span className="pc-rev">@{entry.revision}</span>
              <span className="pc-state" data-state={entry.state}>{memory.states[entry.state]}</span>
            </div>
          ))}
        </div>
        <div className="pc-memory-detail">
          <p className="pc-ref">atomic-memory/{open.id}@{open.revision}</p>
          <p className="pc-memory-title">{open.title}</p>
          <p className="pc-memory-body">{memory.body}</p>
          <p className="pc-memory-label">{memory.sources_label}</p>
          <ul>
            {memory.sources.map((source) => (
              <li key={source}><FileText aria-hidden="true" strokeWidth={1.75} /><span>{source}</span></li>
            ))}
          </ul>
        </div>
      </div>
    </MockWindow>
  );
}

function HandoffMock({ handoff }: { handoff: Features['handoff'] }) {
  return (
    <MockWindow agent={agentBySlug('claude-code')} title={handoff.window}>
      <div className="pc-panel">
        <p className="pc-ref">{handoff.ref}</p>
        <Fields fields={handoff.fields} />
      </div>
    </MockWindow>
  );
}

function ReviewMock({ review }: { review: Features['review'] }) {
  return (
    <MockWindow title={review.window}>
      <div className="pc-panel">
        <p className="pc-panel-kind">{review.kind}</p>
        <Fields fields={review.fields} />
        <div className="pc-panel-actions">
          <span className="pc-mock-button" data-primary="">{review.approve}</span>
          <span className="pc-mock-button">{review.reject}</span>
        </div>
      </div>
    </MockWindow>
  );
}

function SkillMock({ skill }: { skill: Features['skill'] }) {
  return (
    <MockWindow title={skill.window}>
      <div className="pc-panel">
        <p className="pc-skill-name">{skill.name}</p>
        <p className="pc-skill-description">{skill.description}</p>
        <p className="pc-memory-label">{skill.checks_label}</p>
        <ul className="pc-checks">
          {skill.checks.map((check) => (
            <li key={check}><Check aria-hidden="true" strokeWidth={2.25} /><span>{check}</span></li>
          ))}
        </ul>
        <div className="pc-skill-foot">
          <span>{skill.status}</span>
          <span className="pc-skill-export">
            <AgentIcon agent={agentBySlug('codex')} lazy size={16} />{skill.export}
          </span>
        </div>
      </div>
    </MockWindow>
  );
}

function RecallMock({ recall }: { recall: Features['recall'] }) {
  return (
    <MockWindow title={recall.window} trailing={<Chip>{recall.status}</Chip>}>
      <div className="pc-panel">
        <div className="pc-budget-head">
          <span>{recall.budget_label}</span>
          <span className="pc-rev">{recall.budget}</span>
        </div>
        <div aria-hidden="true" className="pc-budget-bar">
          {recall.sections.map((section, index) => (
            <span data-tone={index} key={section.name} style={{ width: `${section.share}%` }} />
          ))}
        </div>
        <ul className="pc-budget-list">
          {recall.sections.map((section, index) => (
            <li key={section.name}>
              <span aria-hidden="true" className="pc-budget-key" data-tone={index} />
              <span>{section.name}</span>
              <span className="pc-rev">{section.size}</span>
            </li>
          ))}
        </ul>
      </div>
    </MockWindow>
  );
}

/**
 * One feature: the mock floats on the shared photograph and the title is its
 * caption, bottom-left over the ink scrim. The mock is a picture of the
 * product, so it is inert and labelled as an image.
 */
function FeatureCard({ title, label, wide = false, children }: {
  title: string;
  label: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <li className="pc-tour-card" data-wide={wide ? '' : undefined}>
      <Backdrop id="lupine" />
      <div aria-label={label} className="pc-tour-mock" role="img">
        <div data-reveal="" inert>{children}</div>
      </div>
      <div className="pc-tour-caption">
        <h3>{title}</h3>
      </div>
    </li>
  );
}

export function FeaturesSection({ content }: { content: Features }) {
  const { memory, handoff, review, skill, recall } = content;
  return (
    <section aria-labelledby="pc-features-title" className="pc-section">
      <SectionHead id="pc-features-title" lead={content.lead} title={content.title} />
      <ul className="pc-tour">
        <FeatureCard label={handoff.label} title={handoff.title}><HandoffMock handoff={handoff} /></FeatureCard>
        <FeatureCard label={memory.label} title={memory.title} wide>
          <MemoryMock memory={memory} scope={content.scope} />
        </FeatureCard>
        <FeatureCard label={review.label} title={review.title}><ReviewMock review={review} /></FeatureCard>
        <FeatureCard label={skill.label} title={skill.title}><SkillMock skill={skill} /></FeatureCard>
        <FeatureCard label={recall.label} title={recall.title}><RecallMock recall={recall} /></FeatureCard>
      </ul>
    </section>
  );
}

/* Ecosystem ---------------------------------------------------------------- */

export function EcosystemSection({ content, copy, lang }: {
  content: HomeContent['ecosystem'];
  copy: Pick<HomeContent['onboarding'], 'copy_label' | 'copied_label'>;
  lang: Language;
}) {
  return (
    <section aria-labelledby="pc-ecosystem-title" className="pc-section">
      <SectionHead id="pc-ecosystem-title" lead={content.lead} title={content.title}>
        <Link className="pc-button pc-button-quiet" href={`/${lang}/docs/integrations`}>
          {content.all_agents_label} <ArrowRight aria-hidden="true" strokeWidth={2} />
        </Link>
      </SectionHead>
      <ul className="pc-grid pc-agents" data-reveal="">
        {agents.map((agent) => (
          <li key={agent.slug}>
            <Link
              aria-label={`${content.docs_label}: ${agent.name}`}
              className="pc-agent"
              href={`/${lang}/docs/integrations/${agent.slug}`}
            >
              <AgentIcon agent={agent} lazy size={36} />
              <span className="pc-agent-text">
                <strong>{agent.name}</strong>
                <span className="pc-agent-how">{agent.integration}</span>
              </span>
              <span className="pc-agent-status">{content.status[agent.status]}</span>
              <ArrowUpRight aria-hidden="true" className="pc-agent-arrow" strokeWidth={1.75} />
            </Link>
          </li>
        ))}
      </ul>
      <div className="pc-connect" data-reveal="">
        <div className="pc-connect-setup">
          <h3>{content.setup_title}</h3>
          <p>{content.setup_lead}</p>
          <CopyCommand command={SETUP_COMMAND} copiedLabel={copy.copied_label} copyLabel={copy.copy_label} />
        </div>
        <div className="pc-connect-interfaces">
          <h3>{content.interfaces_title}</h3>
          <ul>
            {content.interfaces.map((item) => (
              <li key={item.name}>
                <Link href={normalizeHref(item.href)}>
                  <strong>{item.name}</strong>
                  <span>{item.description}</span>
                  <ArrowRight aria-hidden="true" strokeWidth={1.75} />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

/* Proof --------------------------------------------------------------------- */

export function ProofSection({ content }: { content: HomeContent['proof'] }) {
  return (
    <section aria-labelledby="pc-proof-title" className="pc-section">
      <SectionHead id="pc-proof-title" lead={content.lead} title={content.title}>
        <Link className="pc-button pc-button-quiet" href={normalizeHref(content.link.href)}>
          {content.link.label} <ArrowRight aria-hidden="true" strokeWidth={2} />
        </Link>
      </SectionHead>
      <dl className="pc-stats" data-reveal="">
        {content.stats.map((stat) => (
          <div key={stat.label}>
            <dt>{stat.label}</dt>
            <dd className="pc-display">{stat.value}</dd>
            <dd className="pc-stat-detail">{stat.detail}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

/* Onboarding ---------------------------------------------------------------- */

export function OnboardingSection({ content, lang }: { content: HomeContent['onboarding']; lang: Language }) {
  return (
    <section aria-labelledby="pc-start-title" className="pc-section">
      <SectionHead id="pc-start-title" lead={content.lead} title={content.title}>
        <Link className="pc-button pc-button-primary" href={`/${lang}/docs/get-started/quickstart`}>
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
