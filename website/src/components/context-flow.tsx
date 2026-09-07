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

import { BrandLogo } from '@/components/brand-logo';

type ContextFlowProps = {
  label: string;
  steps: Array<{
    items: string[];
    title: string;
  }>;
};

function SessionTape({
  active = false,
  items,
  title,
}: ContextFlowProps['steps'][number] & { active?: boolean }) {
  return (
    <section className="relative z-10 overflow-hidden border border-fd-border bg-fd-background">
      <header className="flex items-center justify-between border-b border-fd-border bg-fd-muted/40 px-3 py-3 sm:px-4">
        <h3 className="font-mono text-xs font-medium">{title}</h3>
        <span aria-hidden="true" className="flex items-center gap-1">
          <span className="size-1 bg-fd-muted-foreground/35" />
          <span className="size-1 bg-fd-muted-foreground/35" />
          <span className="size-1 bg-fd-muted-foreground/35" />
        </span>
      </header>

      <ol aria-label={title} className="font-mono text-xs" role="log">
        {items.map((item, index) => {
          const highlighted = active ? index === 0 : index === items.length - 1;
          return (
            <li
              className="grid min-h-10 grid-cols-[auto_1fr] items-center gap-2 border-b border-fd-border/70 px-3 last:border-b-0 sm:grid-cols-[2rem_auto_1fr] sm:gap-3 sm:px-4"
              key={item}
            >
              <span className="hidden tabular-nums text-fd-muted-foreground/50 sm:block">
                {String(index + 1).padStart(2, '0')}
              </span>
              <span
                aria-hidden="true"
                className={[
                  'size-1.5',
                  highlighted ? 'pc-session-entry bg-fd-primary' : 'bg-fd-muted-foreground/30',
                ].join(' ')}
              />
              <span className={highlighted ? 'text-fd-foreground' : 'text-fd-muted-foreground'}>{item}</span>
            </li>
          );
        })}
      </ol>

      <footer aria-hidden="true" className="flex h-8 items-center border-t border-fd-border px-4">
        {active ? <span className="pc-session-cursor h-3 w-px bg-fd-primary" /> : null}
      </footer>
    </section>
  );
}

function ContextArch({ title }: { title: string }) {
  return (
    <>
      <svg
        aria-hidden="true"
        className="absolute inset-x-0 top-8 h-20 w-full overflow-visible"
        preserveAspectRatio="none"
        viewBox="0 0 100 32"
      >
        <path
          className="fill-none stroke-fd-border"
          d="M25 32 Q50 0 75 32"
          pathLength="100"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
        <path
          className="pc-context-arc-flow fill-none stroke-fd-primary"
          d="M25 32 Q50 0 75 32"
          pathLength="100"
          strokeWidth="2"
          vectorEffect="non-scaling-stroke"
        />
        <circle className="fill-fd-primary" cx="25" cy="32" r="0.7" />
        <circle className="fill-fd-primary" cx="75" cy="32" r="0.7" />
      </svg>

      <div className="absolute inset-x-0 top-2 z-10 flex justify-center">
        <div className="pc-context-breathe bg-fd-muted px-4 py-3">
          <BrandLogo className="w-32" />
          <h3 className="sr-only">{title}</h3>
        </div>
      </div>
    </>
  );
}

export function ContextFlow({ label, steps }: ContextFlowProps) {
  const [currentSession, context, nextSession] = steps;

  return (
    <figure aria-label={label} className="overflow-hidden bg-fd-muted/30 px-3 py-8 sm:px-8 sm:py-10">
      <div className="relative pt-28">
        <ContextArch title={context.title} />
        <div className="grid grid-cols-2 items-stretch gap-3 sm:gap-8">
          <div className="h-full w-full max-w-60 justify-self-center">
            <SessionTape {...currentSession} />
          </div>
          <div className="h-full w-full max-w-60 justify-self-center">
            <SessionTape {...nextSession} active />
          </div>
        </div>
      </div>
    </figure>
  );
}
