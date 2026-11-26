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

import { X } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function PersistentGuide({
  icon,
  title,
  description,
  action,
  onAction,
  secondaryAction,
  onSecondaryAction,
  closeLabel,
  onClose,
  tone = "default",
}: {
  icon: ReactNode;
  title: string;
  description?: string;
  action?: string;
  onAction?: () => void;
  secondaryAction?: string;
  onSecondaryAction?: () => void;
  closeLabel: string;
  onClose: () => void;
  tone?: "default" | "success";
}) {
  const semanticTone = tone === "success";

  return (
    <aside
      data-slot="persistent-guide"
      data-tone={tone}
      className={cn(
        "fixed right-4 bottom-4 z-[var(--pc-z-index-sticky)] grid w-[360px] max-w-[calc(100vw-32px)] grid-cols-[36px_minmax(0,1fr)_32px] items-center gap-x-3 gap-y-2 overflow-hidden rounded-lg border border-border bg-card p-4 shadow-[var(--pc-shadows-floating)]",
        semanticTone && "grid-cols-[20px_minmax(0,1fr)_32px]",
      )}
      aria-live="polite"
    >
      <span
        className={cn(
          "grid size-9 place-items-center rounded-md bg-accent text-foreground [&_svg]:size-[18px]",
          semanticTone &&
            "size-5 rounded-none bg-transparent text-[var(--success)] [&_svg]:size-5",
        )}
        aria-hidden="true"
      >
        {icon}
      </span>
      <strong className="min-w-0 overflow-wrap-anywhere text-[length:var(--pc-text-action)] leading-5 font-medium text-foreground">
        {title}
      </strong>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className="shrink-0 text-muted-foreground"
        aria-label={closeLabel}
        onClick={onClose}
      >
        <X aria-hidden="true" />
      </Button>
      {(description || action || secondaryAction) && (
        <div className="col-span-2 col-start-2 min-w-0 overflow-wrap-anywhere">
          {description && (
            <p className="mb-2 text-[length:var(--pc-text-action)] leading-5 text-muted-foreground last:mb-0">
              {description}
            </p>
          )}
          {(action || secondaryAction) && (
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {action && onAction && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-auto min-h-0 px-0 py-1 text-[var(--info)] hover:bg-transparent hover:text-[var(--info)]/80"
                  onClick={onAction}
                >
                  {action}
                </Button>
              )}
              {secondaryAction && onSecondaryAction && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-auto min-h-0 px-0 py-1 text-[var(--info)] hover:bg-transparent hover:text-[var(--info)]/80"
                  onClick={onSecondaryAction}
                >
                  {secondaryAction}
                </Button>
              )}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
