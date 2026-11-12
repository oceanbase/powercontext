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

import { useImperativeHandle, type Ref } from 'react';

/** Which docking station the turntable presents the Scope tray to. */
export type HandoffSide = 'from' | 'to';

export type HandoffFigureHandle = {
  /** Plays the opening once: the turntable turns 180° from `from` to `to` while the figure grows into place. */
  play: () => Promise<void>;
  /** Turns the table to one station; resolves when it rests. */
  turnTo: (side: HandoffSide) => Promise<void>;
};

export type HandoffFigureLabels = {
  /** Accessible description of the whole figure. */
  description: string;
  /** Card chrome: figure number, title, interaction hint. */
  figure: string;
  title: string;
  hint: string;
  /** Live readout text per resting side. */
  readout: Record<HandoffSide, string>;
};

/**
 * The isometric handoff turntable. Placeholder contract: the real figure
 * replaces this body and keeps the exported names and props.
 */
export function HandoffFigure({
  labels, autoplay = false, className, ref,
}: {
  labels: HandoffFigureLabels;
  /** Play the opening on mount instead of waiting for `play()`. */
  autoplay?: boolean;
  className?: string;
  ref?: Ref<HandoffFigureHandle>;
}) {
  useImperativeHandle(ref, () => ({ play: async () => {}, turnTo: async () => {} }), []);
  return <div role="img" aria-label={labels.description} data-autoplay={autoplay} className={className} />;
}
