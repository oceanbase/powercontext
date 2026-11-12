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

/**
 * The home opening: agent cursors finish the hero card. The server renders
 * the final hero. Before the first paint, an inline script decides whether the
 * opening plays and, if so, marks <html> so home.css shows the draft. The
 * client claims the draft, animates it to the final hero and clears every
 * trace. The same script turns on reveal-on-scroll for the sections below.
 */

/** On <html>: `pending` until the client claims the draft, then `running`. */
export const INTRO_ATTR = 'data-pc-intro';
/** On <html> while sections below the hero wait to be revealed: `pending`, then `live`. */
export const REVEAL_ATTR = 'data-pc-reveal';
/** The opening plays once per browser tab session. */
export const PLAYED_KEY = 'powercontext:home-intro';
/** Marks the hero parts the opening moves. */
export const PART_ATTR = 'data-intro';
/** A slow load drops the draft and shows the final hero. */
const CLAIM_TIMEOUT_MS = 3000;
const REDUCED_MEDIA = '(prefers-reduced-motion: reduce)';

/**
 * Runs before the hero paints. Storage can throw when the browser blocks it;
 * the opening then does not play.
 */
export const introScript = `(function(){var h=document.documentElement;
function drop(a){setTimeout(function(){if(h.getAttribute(a)==="pending")h.removeAttribute(a)},${CLAIM_TIMEOUT_MS})}
try{if(window.matchMedia(${JSON.stringify(REDUCED_MEDIA)}).matches)return;
if("IntersectionObserver" in window){h.setAttribute(${JSON.stringify(REVEAL_ATTR)},"pending");drop(${JSON.stringify(REVEAL_ATTR)})}
if(window.scrollY>40||sessionStorage.getItem(${JSON.stringify(PLAYED_KEY)}))return;
h.setAttribute(${JSON.stringify(INTRO_ATTR)},"pending");drop(${JSON.stringify(INTRO_ATTR)});
}catch(e){}})();`;
