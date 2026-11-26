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

import type { ComponentProps } from "react";
import { Slot } from "radix-ui";
import { cn } from "@/lib/utils";

// Shared state precedence: invalid stays visible during hover, focus and open.
export const fieldStateClasses =
  "outline-none transition-colors placeholder:text-[var(--text-tertiary)] placeholder:opacity-100 enabled:hover:border-border-strong focus-visible:border-primary aria-invalid:border-destructive aria-invalid:hover:border-destructive aria-invalid:focus-visible:border-destructive disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground";

export const readOnlyFieldClasses =
  "read-only:cursor-text read-only:bg-muted read-only:text-muted-foreground";

export function FieldGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="field-group"
      className={cn("grid gap-4", className)}
      {...props}
    />
  );
}

export function Field({ className, ...props }: ComponentProps<"div">) {
  return (
    <div data-slot="field" className={cn("grid gap-2", className)} {...props} />
  );
}

export function FieldLabel({ className, ...props }: ComponentProps<"label">) {
  return (
    <label
      data-slot="field-label"
      className={cn(
        "grid gap-2 text-[length:var(--pc-text-action)] leading-[var(--pc-leading-action)] font-normal text-foreground disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}

export function FieldDescription({ className, ...props }: ComponentProps<"p">) {
  return (
    <p
      data-slot="field-description"
      className={cn(
        "m-0 text-[length:var(--pc-text-caption)] leading-[var(--pc-leading-caption)] text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function FieldError({
  className,
  asChild = false,
  ...props
}: ComponentProps<"p"> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "p";
  return (
    <Comp
      data-slot="field-error"
      className={cn(
        "m-0 text-[length:var(--pc-text-caption)] leading-[var(--pc-leading-caption)] text-destructive",
        className,
      )}
      {...props}
    />
  );
}
