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

import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  Toast,
  ToastProvider,
  ToastViewport,
  ToastTitle,
  ToastDescription,
  ToastClose,
} from "./toast";

export const ACTION_NOTIFICATION_AUTO_DISMISS_MS = 6_000;

export function ActionNotification({
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
  tone?: "default" | "success" | "error";
}) {
  const semanticTone = tone === "success" || tone === "error";

  return (
    <ToastProvider
      duration={ACTION_NOTIFICATION_AUTO_DISMISS_MS}
      label="Notification"
    >
      <Toast
        key={title}
        open
        onOpenChange={(open) => {
          if (!open) onClose();
        }}
        type={tone === "error" ? "foreground" : "background"}
        data-slot="action-notification"
        className={cn(
          "grid grid-cols-[36px_minmax(0,1fr)_auto] items-start gap-x-3 gap-y-2",
          semanticTone && "grid-cols-[20px_minmax(0,1fr)_auto] px-4 py-2",
        )}
        data-tone={tone}
        data-has-description={!!description}
      >
        <span
          className={cn(
            "grid size-9 place-items-center rounded-md bg-accent text-foreground [&_svg]:size-[18px]",
            semanticTone &&
              "mt-1.5 size-5 rounded-none bg-transparent [&_svg]:size-5",
            tone === "success" && "text-[var(--success)]",
            tone === "error" && "text-destructive",
          )}
          aria-hidden="true"
        >
          {icon}
        </span>
        <ToastTitle asChild>
          <strong
            className={cn(
              "min-w-0 pt-2 text-[length:var(--pc-text-action)] leading-5 font-medium text-foreground overflow-wrap-anywhere",
              semanticTone && "pt-1.5",
            )}
          >
            {title}
          </strong>
        </ToastTitle>
        <ToastClose asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className={cn(
              "mt-0.5 shrink-0 text-muted-foreground",
              semanticTone && "mt-0",
            )}
            aria-label={closeLabel}
          >
            <X aria-hidden="true" />
          </Button>
        </ToastClose>
        {(description || action || secondaryAction) && (
          <div className="col-span-2 col-start-2 min-w-0 overflow-wrap-anywhere">
            {description && (
              <ToastDescription asChild>
                <p className="mb-3 text-[length:var(--pc-text-action)] leading-5 text-muted-foreground last:mb-0">
                  {description}
                </p>
              </ToastDescription>
            )}
            {(action || secondaryAction) && (
              <div className="flex flex-wrap gap-4">
                {action && onAction && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="px-0 text-[var(--info)]"
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
                    className="px-0 text-[var(--info)]"
                    onClick={onSecondaryAction}
                  >
                    {secondaryAction}
                  </Button>
                )}
              </div>
            )}
          </div>
        )}
      </Toast>
      <ToastViewport />
    </ToastProvider>
  );
}
