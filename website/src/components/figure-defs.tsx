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

/*
 * Arrowheads for the figure kit, defined once per article. A wire ends in one
 * with marker-end="url(#fig-arrow)" (ink), "url(#fig-arrow-accent)" or
 * "url(#fig-arrow-muted)". Each head scales with its wire's stroke width.
 */
const heads = [
  { id: 'fig-arrow', fill: 'var(--fig-line)' },
  { id: 'fig-arrow-accent', fill: 'var(--fig-accent)' },
  { id: 'fig-arrow-muted', fill: 'var(--fig-faint)' },
];

export function FigureDefs() {
  return (
    <svg aria-hidden="true" focusable="false" height="0" style={{ position: 'absolute' }} width="0">
      <defs>
        {heads.map((head) => (
          <marker
            id={head.id}
            key={head.id}
            markerHeight="4.4"
            markerWidth="4.4"
            orient="auto-start-reverse"
            refX="8.5"
            refY="5"
            viewBox="0 0 10 10"
          >
            <path d="M0.5 0.8 L9.5 5 L0.5 9.2 Z" fill={head.fill} />
          </marker>
        ))}
      </defs>
    </svg>
  );
}
