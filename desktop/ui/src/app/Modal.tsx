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

import { useLayoutEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

// Nested dialogs can close in the same React commit, in either cleanup order.
const backgrounds = new WeakMap<
  HTMLElement,
  { count: number; inert: boolean; hidden: string | null }
>();

export function Modal({
  label,
  role = "dialog",
  onClose,
  descriptionId,
  returnFocus,
  children,
}: {
  label: string;
  role?: "dialog" | "alertdialog";
  onClose: () => void;
  descriptionId?: string;
  returnFocus?: () => HTMLElement | null;
  children: ReactNode;
}) {
  const backdrop = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const restore = useRef(returnFocus);
  restore.current = returnFocus;
  const close = useRef(onClose);
  close.current = onClose;
  useLayoutEffect(() => {
    const previous = document.activeElement;
    const background = Array.from(document.body.children).filter(
      (element): element is HTMLElement =>
        element instanceof HTMLElement && element !== backdrop.current,
    );
    background.forEach((element) => {
      const saved = backgrounds.get(element) ?? {
        count: 0,
        inert: element.inert,
        hidden: element.getAttribute("aria-hidden"),
      };
      saved.count++;
      backgrounds.set(element, saved);
      element.inert = true;
      element.setAttribute("aria-hidden", "true");
    });
    function onKeyDown(event: KeyboardEvent) {
      if (backdrop.current?.inert) return;
      if (event.key === "Escape") {
        event.stopPropagation();
        event.preventDefault();
        close.current();
      }
      if (event.key !== "Tab") return;
      const controls = Array.from(
        dialog.current?.querySelectorAll<HTMLElement>(
          'button, input, select, textarea, a[href], [tabindex="0"]',
        ) ?? [],
      ).filter(
        (element) =>
          !element.matches(":disabled") && !element.closest("[hidden]"),
      );
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (
        (!event.shiftKey && document.activeElement === last) ||
        !dialog.current?.contains(document.activeElement)
      ) {
        event.preventDefault();
        first?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    dialog.current?.querySelector<HTMLElement>("button")?.focus();
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      background.forEach((element) => {
        const saved = backgrounds.get(element)!;
        if (--saved.count > 0) return;
        backgrounds.delete(element);
        element.inert = saved.inert;
        if (saved.hidden === null) element.removeAttribute("aria-hidden");
        else element.setAttribute("aria-hidden", saved.hidden);
      });
      const target = restore.current ? restore.current() : previous;
      if (target === null) return;
      const fallback =
        Array.from(
          document.querySelectorAll<HTMLElement>(
            '[role="dialog"] button:not(:disabled)',
          ),
        ).find(
          (element) =>
            !backdrop.current?.contains(element) &&
            !element.closest('[inert], [aria-hidden="true"]'),
        ) ?? document.querySelector<HTMLElement>("main h1");
      for (const candidate of [target, fallback]) {
        if (
          candidate instanceof HTMLElement &&
          candidate.isConnected &&
          candidate !== document.body &&
          candidate !== document.documentElement &&
          !candidate.matches(":disabled") &&
          !candidate.closest('[inert], [aria-hidden="true"]')
        ) {
          candidate.focus();
          if (document.activeElement === candidate) break;
        }
      }
    };
  }, []);
  return createPortal(
    <div
      className="modal-backdrop"
      ref={backdrop}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role={role}
        aria-modal="true"
        aria-label={label}
        aria-describedby={descriptionId}
        ref={dialog}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
