/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { cva } from "class-variance-authority";
import {
  CircleCheck,
  CircleX,
  Inbox,
  Info,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";
import type { ComponentProps, ReactNode } from "react";

import { cn } from "@/lib/utils";
import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";

export type FeedbackKind =
  | "loading"
  | "empty"
  | "unavailable"
  | "error"
  | "success"
  | "warning";

const feedbackVariants = cva(
  "min-w-0 overflow-wrap-anywhere border text-left text-[length:var(--pc-text-action)] leading-[var(--pc-leading-action)] forced-colors:border-[CanvasText]",
  {
    variants: {
      compact: {
        true: "grid gap-1 rounded-md px-3 py-2",
        false: "grid gap-4 rounded-lg bg-card p-6",
      },
      kind: {
        loading: "border-border bg-muted",
        empty: "border-border bg-card",
        unavailable: "border-border bg-muted",
        error: "",
        success: "",
        warning: "",
      },
    },
    defaultVariants: { compact: false, kind: "empty" },
  },
);

const iconByKind = {
  loading: LoaderCircle,
  empty: Inbox,
  unavailable: TriangleAlert,
  error: CircleX,
  success: CircleCheck,
  warning: TriangleAlert,
} as const;

export function PageFeedback({
  kind,
  title,
  children,
  action,
  compact = false,
  headingLevel,
  className,
}: {
  kind: FeedbackKind;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  headingLevel?: 3;
  className?: string;
}) {
  const Container = compact ? "div" : "section";
  const Title = headingLevel === 3 ? "h3" : compact ? "p" : "h2";
  const Icon = iconByKind[kind];
  const role =
    kind === "error"
      ? "alert"
      : kind === "loading" || kind === "success" || kind === "warning"
        ? "status"
        : undefined;

  if (kind === "error" || kind === "success" || kind === "warning") {
    return (
      <Alert
        asChild
        variant={kind === "error" ? "destructive" : kind}
        size={compact ? "compact" : "default"}
        className={className}
        role={role}
        aria-live={role === "status" ? "polite" : undefined}
      >
        <Container data-slot="feedback" data-kind={kind}>
          <Icon aria-hidden="true" strokeWidth={1.75} />
          <AlertTitle asChild>
            <Title>{title}</Title>
          </AlertTitle>
          {children && (
            <AlertDescription className="grid gap-1">
              {children}
            </AlertDescription>
          )}
          {action && (
            <div className="col-start-2 flex flex-wrap gap-2">{action}</div>
          )}
        </Container>
      </Alert>
    );
  }

  return (
    <Container
      data-slot="feedback"
      data-kind={kind}
      className={cn(feedbackVariants({ compact, kind }), className)}
      role={role}
      aria-live={role === "status" ? "polite" : undefined}
      aria-busy={kind === "loading" || undefined}
    >
      <Title className="m-0 flex min-w-0 items-center gap-1.5 text-[length:var(--pc-text-action)] leading-[var(--pc-leading-action)] font-medium text-foreground">
        <Icon
          aria-hidden="true"
          className={cn(
            "size-[var(--pc-controls-icon-md)] shrink-0 [stroke-width:var(--pc-controls-icon-stroke)]",
            kind === "loading" &&
              "motion-safe:animate-spin motion-reduce:animate-none",
            (kind === "empty" || kind === "unavailable") &&
              "text-muted-foreground",
          )}
        />
        <span className="min-w-0">{title}</span>
      </Title>
      {children && (
        <div className="grid gap-1 pl-[calc(var(--pc-controls-icon-md)+var(--pc-space-1-5))] text-muted-foreground [&_p]:m-0">
          {children}
        </div>
      )}
      {action && (
        <div className="flex flex-wrap gap-2 pl-[calc(var(--pc-controls-icon-md)+var(--pc-space-1-5))]">
          {action}
        </div>
      )}
    </Container>
  );
}

export function InlineNote({
  children,
  className,
  ...props
}: ComponentProps<"p">) {
  return (
    <p
      data-slot="inline-note"
      className={cn(
        "m-0 flex items-start gap-2 text-[length:var(--pc-text-action)] leading-[var(--pc-leading-action)] text-muted-foreground",
        className,
      )}
      {...props}
    >
      <Info
        aria-hidden="true"
        className="mt-0.5 size-[var(--pc-controls-icon-md)] shrink-0"
        strokeWidth={1.75}
      />
      <span className="min-w-0">{children}</span>
    </p>
  );
}

export { feedbackVariants };
