import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-sm border text-[length:var(--pc-text-caption)] leading-none",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary text-primary-foreground",
        secondary: "border-border bg-muted text-muted-foreground",
        tertiary: "border-border bg-muted text-[var(--text-tertiary)]",
        success:
          "border-transparent bg-[var(--success-bg)] text-[var(--success-text)]",
        outline: "border-border bg-card text-card-foreground",
      },
      size: {
        default: "min-h-6 px-2.5 py-0.5 font-medium",
        compact: "min-h-5 px-2 py-0 font-normal",
      },
    },
    defaultVariants: { variant: "secondary", size: "default" },
  },
);

function Badge({
  className,
  variant,
  size,
  ...props
}: ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ variant, size }), className)}
      {...props}
    />
  );
}

export { Badge, badgeVariants };
