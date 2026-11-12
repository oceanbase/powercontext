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

import { Check, CircleCheck } from 'lucide-react';
import type { ReactNode } from 'react';
import type { HomeContent, HomeSessionWindow } from '@/lib/home-content';
import { AgentIcon, type Agent } from './agents';

export type SessionSide = 'ending' | 'next';

/** A solid app window: title bar with three muted dots over a card surface. */
export function MockWindow({ agent, title, trailing, children, className }: {
  agent?: Agent;
  title: string;
  trailing?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`pc-window ${className ?? ''}`}>
      <div className="pc-window-bar">
        <span aria-hidden="true" className="pc-window-dots"><span /><span /><span /></span>
        <span className="pc-window-title">
          {agent ? <AgentIcon agent={agent} size={16} /> : null}
          <span>{title}</span>
        </span>
        {trailing}
      </div>
      {children}
    </div>
  );
}

/** The depicted control each agent presses; its label swaps once pressed. */
function WindowAction({ copy }: { copy: HomeSessionWindow }) {
  return (
    <span className="pc-window-action" data-action="">
      <span className="pc-action-ready">{copy.action}</span>
      <span className="pc-action-done"><Check aria-hidden="true" strokeWidth={2} />{copy.action_done}</span>
    </span>
  );
}

function Result({ label, value }: { label: string; value: string }) {
  return (
    <p className="pc-line-result">
      <CircleCheck aria-hidden="true" strokeWidth={2} />
      <span><b>{label}</b> {value}</span>
    </p>
  );
}

/**
 * The two agent sessions on the figure's corners: the ending one prepares a
 * Handoff, the next one acknowledges it and lists what it received. Each
 * renders its final state; home.css shows the draft while the opening runs,
 * until the session is marked `data-done`.
 */
export function SessionWindow({ side, copy, agent }: {
  side: SessionSide;
  copy: HomeContent['hero']['sessions'];
  agent: Agent;
}) {
  if (side === 'ending') {
    const ending = copy.ending;
    const [prompt, ...lines] = ending.lines;
    return (
      <div className="pc-session pc-session-ending" data-session="ending">
        <MockWindow agent={agent} title={ending.title} trailing={<WindowAction copy={ending} />}>
          <div className="pc-window-body">
            <p className="pc-line-prompt">{prompt}</p>
            {lines.map((line) => <p key={line}>{line}</p>)}
            <div className="pc-swap">
              <p className="pc-swap-draft">&nbsp;</p>
              <div className="pc-swap-final"><Result label={ending.result_label} value={ending.result} /></div>
            </div>
          </div>
        </MockWindow>
      </div>
    );
  }

  const next = copy.next;
  return (
    <div className="pc-session pc-session-next" data-session="next">
      <MockWindow agent={agent} title={next.title} trailing={<WindowAction copy={next} />}>
        <div className="pc-window-body">
          <p className="pc-line-prompt">{next.prompt}</p>
          <div className="pc-swap">
            <p className="pc-swap-draft">{next.waiting}</p>
            <div className="pc-swap-final">
              <Result label={next.result_label} value={copy.ending.result} />
              <dl className="pc-received">
                {next.received.map((item) => (
                  <div className="contents" key={item.label}>
                    <dt>{item.label}</dt>
                    <dd>{item.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </div>
      </MockWindow>
    </div>
  );
}
