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

import * as React from "react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;

function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn(
        "col-start-1 row-start-1 m-0 min-w-0 text-left text-[length:var(--pc-text-component-title)] leading-[var(--pc-leading-component-title)] font-medium text-foreground",
        className,
      )}
      {...props}
    />
  );
}

function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn(
        "m-0 text-[length:var(--pc-text-action)] leading-[var(--pc-leading-action)] text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      data-slot="dialog-overlay"
      className={cn(
        "fixed inset-0 z-[var(--pc-z-index-modal-backdrop)] bg-[var(--pc-color-overlay)]",
        className,
      )}
      {...props}
    />
  );
}

function DialogContent({
  className,
  children,
  overlayClassName,
  returnFocus,
  onCloseAutoFocus,
  onInteractOutside,
  onPointerDownOutside,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  overlayClassName?: string;
  returnFocus?: () => HTMLElement | null;
}) {
  const previousFocus = React.useRef<HTMLElement | null>(
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );

  return (
    <DialogPrimitive.Portal>
      <DialogOverlay className={overlayClassName} />
      <div
        data-slot="dialog-positioner"
        className="pointer-events-none fixed inset-0 z-[var(--pc-z-index-modal)] grid place-items-center overflow-y-auto px-[var(--pc-space-6)] py-[var(--pc-space-4)]"
      >
        <DialogPrimitive.Content
          data-slot="dialog-content"
          className={cn(
            "pointer-events-auto relative grid max-h-[calc(100dvh-var(--pc-space-8))] w-full max-w-[var(--pc-layout-dialog-max-width)] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden rounded-lg border border-border bg-card text-card-foreground shadow-[var(--pc-shadows-dialog)] outline-none",
            className,
          )}
          onCloseAutoFocus={(event) => {
            onCloseAutoFocus?.(event);
            if (event.defaultPrevented) return;
            const target = returnFocus?.() ?? previousFocus.current;
            if (!target?.isConnected) return;
            event.preventDefault();
            target.focus({ preventScroll: true });
          }}
          onInteractOutside={(event) => {
            onInteractOutside?.(event);
            if (!event.defaultPrevented) event.preventDefault();
          }}
          onPointerDownOutside={(event) => {
            onPointerDownOutside?.(event);
            if (!event.defaultPrevented) event.preventDefault();
          }}
          {...props}
        >
          {children}
        </DialogPrimitive.Content>
      </div>
    </DialogPrimitive.Portal>
  );
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn(
        "grid min-h-[52px] grid-cols-[minmax(0,1fr)_32px] items-center gap-2 border-b border-border px-6 py-4",
        className,
      )}
      {...props}
    />
  );
}

function DialogBody({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-body"
      className={cn("grid min-h-0 gap-4 overflow-y-auto px-6 py-4", className)}
      {...props}
    />
  );
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn(
        "flex flex-wrap justify-end gap-2 px-6 pt-2 pb-4 [&_[data-slot=button]]:h-[var(--pc-controls-height-compact)] [&_[data-slot=button]]:min-h-[var(--pc-controls-height-compact)] [&_[data-slot=button]]:px-3",
        className,
      )}
      {...props}
    />
  );
}

function DialogCloseButton({
  label,
  className,
}: {
  label: string;
  className?: string;
}) {
  return (
    <DialogClose asChild>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        className={cn("col-start-2 row-start-1", className)}
        aria-label={label}
      >
        <X aria-hidden="true" />
      </Button>
    </DialogClose>
  );
}

export {
  Dialog,
  DialogClose,
  DialogContent,
  DialogBody,
  DialogCloseButton,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
};
