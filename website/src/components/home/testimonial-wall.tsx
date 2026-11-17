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

import { Pause, Play } from 'lucide-react';
import { useState, type CSSProperties } from 'react';
import type { HomeContent } from '@/lib/home-content';
import type { Testimonial } from './testimonials';

/** Seconds the row takes to move by one card. */
const SECONDS_PER_CARD = 11;

function XMark() {
  return (
    <svg aria-hidden="true" className="pc-quote-x" fill="currentColor" viewBox="0 0 24 24">
      <path d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
    </svg>
  );
}

/**
 * One post as X's display rules ask: avatar, display name, @handle and the
 * X logo with the text. The whole card links to the author's profile through
 * the name.
 */
function TestimonialCard({ testimonial, profileLabel }: { testimonial: Testimonial; profileLabel: string }) {
  return (
    <figure className="pc-quote">
      <figcaption>
        <img alt="" height={40} loading="lazy" src={testimonial.avatar} width={40} />
        <span className="pc-quote-author">
          <a
            aria-label={profileLabel.replace('{name}', testimonial.name)}
            href={testimonial.href}
            rel="noopener noreferrer"
            target="_blank"
          >
            {testimonial.name}
          </a>
          <span>@{testimonial.handle}</span>
        </span>
        <XMark />
      </figcaption>
      <blockquote lang={testimonial.lang}>
        <p>{testimonial.text}</p>
      </blockquote>
    </figure>
  );
}

function Cards({ testimonials, profileLabel, repeat = false }: {
  testimonials: Testimonial[];
  profileLabel: string;
  repeat?: boolean;
}) {
  // The repeat only closes the loop: screen readers and the tab order skip it.
  return (
    <ul aria-hidden={repeat || undefined} className="pc-quotes-set" data-repeat={repeat ? '' : undefined} inert={repeat || undefined}>
      {testimonials.map((testimonial) => (
        <li key={testimonial.handle}>
          <TestimonialCard profileLabel={profileLabel} testimonial={testimonial} />
        </li>
      ))}
    </ul>
  );
}

/**
 * Posts about PowerContext on X, drifting in one row. Hovering or focusing
 * the row pauses it, and the button pauses it for good (WCAG 2.2.2). Under
 * reduced motion the row stands still and scrolls sideways instead.
 */
export function TestimonialsSection({ content, testimonials }: {
  content: HomeContent['testimonials'];
  testimonials: Testimonial[];
}) {
  const [paused, setPaused] = useState(false);
  const label = paused ? content.play : content.pause;
  return (
    <section aria-labelledby="pc-testimonials-title" className="pc-quotes" data-paused={paused ? '' : undefined}>
      <header className="pc-wrap pc-quotes-head" data-reveal="">
        <h2 className="pc-display pc-section-title" id="pc-testimonials-title">{content.title}</h2>
        <button aria-label={label} className="pc-quotes-toggle" onClick={() => setPaused((value) => !value)} title={label} type="button">
          {paused ? <Play aria-hidden="true" strokeWidth={1.75} /> : <Pause aria-hidden="true" strokeWidth={1.75} />}
        </button>
      </header>
      <div className="pc-quotes-row" data-reveal="">
        <div
          className="pc-quotes-track"
          style={{ '--pc-marquee': `${testimonials.length * SECONDS_PER_CARD}s` } as CSSProperties}
        >
          <Cards profileLabel={content.profile} testimonials={testimonials} />
          <Cards profileLabel={content.profile} repeat testimonials={testimonials} />
        </div>
      </div>
    </section>
  );
}
