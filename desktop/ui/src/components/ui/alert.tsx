/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const alertVariants = cva(
  "relative grid min-w-0 w-full grid-cols-[auto_1fr] items-start gap-x-2 overflow-wrap-anywhere border text-left text-[length:var(--pc-text-action)] leading-[var(--pc-leading-action)] forced-colors:border-[CanvasText] [&>svg]:mt-0.5 [&>svg]:size-[var(--pc-controls-icon-md)] [&>svg]:shrink-0 [&>svg]:[stroke-width:var(--pc-controls-icon-stroke)]",
  {
    variants: {
      variant: {
        default:
          "border-border bg-card text-card-foreground [--alert-title-color:var(--card-foreground)]",
        destructive:
          "border-destructive bg-[var(--destructive-bg)] text-destructive [--alert-title-color:var(--destructive)]",
        success:
          "border-[var(--success)] bg-[var(--success-bg)] text-[var(--success-text)] [--alert-title-color:var(--success-text)]",
        warning:
          "border-[var(--border-strong)] bg-muted text-[var(--pc-color-warning)] [--alert-title-color:var(--pc-color-warning)]",
      },
      size: {
        compact: "gap-y-1 rounded-md p-3",
        default: "gap-y-1 rounded-md p-4",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "compact",
    },
  },
);

function Alert({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: ComponentProps<"div"> &
  VariantProps<typeof alertVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "div";
  return (
    <Comp
      data-slot="alert"
      role="alert"
      className={cn(alertVariants({ variant, size }), className)}
      {...props}
    />
  );
}

function AlertTitle({
  className,
  asChild = false,
  ...props
}: ComponentProps<"div"> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "div";
  return (
    <Comp
      data-slot="alert-title"
      className={cn(
        "col-start-2 m-0 min-w-0 text-[length:var(--pc-text-action)] leading-[var(--pc-leading-action)] font-medium text-[var(--alert-title-color)]",
        className,
      )}
      {...props}
    />
  );
}

function AlertDescription({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="alert-description"
      className={cn(
        "col-start-2 min-w-0 text-muted-foreground [&_p]:m-0",
        className,
      )}
      {...props}
    />
  );
}

export { Alert, AlertDescription, AlertTitle, alertVariants };
