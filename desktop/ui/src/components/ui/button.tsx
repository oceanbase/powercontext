/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { Slot } from "radix-ui";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 rounded-md border border-transparent px-4 text-[length:var(--pc-text-action)] font-normal leading-[var(--pc-leading-action)] whitespace-nowrap transition-colors duration-[var(--pc-motion-fast)] ease-out outline-none select-none motion-reduce:transition-none disabled:pointer-events-none disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground focus-visible:ring-[3px] focus-visible:ring-ring focus-visible:ring-offset-[3px] focus-visible:ring-offset-background aria-invalid:border-destructive aria-invalid:ring-destructive/20 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:[stroke-width:var(--pc-controls-icon-stroke)]",
  {
    variants: {
      variant: {
        default:
          "bg-primary text-primary-foreground hover:bg-[var(--accent-hover)] active:bg-[var(--accent-hover)]",
        destructive:
          "border-destructive bg-card text-destructive hover:bg-destructive/10 active:bg-destructive/15",
        outline:
          "border-border bg-card text-card-foreground hover:border-primary hover:bg-card",
        secondary:
          "border-border bg-secondary text-secondary-foreground hover:border-primary hover:bg-accent",
        ghost: "text-foreground hover:bg-accent hover:text-accent-foreground",
        row: "rounded-none border-0 bg-transparent text-foreground hover:bg-muted data-[selected=true]:bg-accent",
        context:
          "rounded-sm bg-transparent px-1 text-foreground hover:bg-accent aria-expanded:bg-accent",
        navigation:
          "justify-start border-transparent bg-transparent px-3 text-foreground hover:bg-[var(--surface-hover)] aria-[current=page]:bg-accent aria-[current=page]:font-medium aria-[current=page]:hover:bg-accent forced-colors:aria-[current=page]:border-[2px] forced-colors:aria-[current=page]:border-[Highlight]",
        segmented:
          "border-transparent bg-transparent text-muted-foreground hover:bg-card hover:text-foreground aria-selected:bg-card aria-selected:text-foreground",
        "field-icon":
          "border-transparent bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground",
        "field-trigger":
          "rounded-l-none border-transparent border-l-border bg-transparent text-muted-foreground hover:bg-accent hover:text-foreground",
        floating:
          "rounded-full border-border-strong bg-card text-card-foreground shadow-[var(--pc-shadows-floating)] hover:bg-muted",
        link: "min-h-0 rounded-none px-0 py-0 text-primary underline-offset-4 hover:underline",
        text: "rounded-none bg-transparent text-muted-foreground hover:text-foreground active:text-foreground",
      },
      size: {
        default:
          "h-[var(--pc-controls-height-default)] min-h-[var(--pc-controls-height-default)]",
        sm: "h-[var(--pc-controls-height-compact)] min-h-[var(--pc-controls-height-compact)] px-3",
        icon: "size-[var(--pc-controls-height-default)] min-h-[var(--pc-controls-height-default)] px-0",
        "icon-sm":
          "size-[var(--pc-controls-height-compact)] min-h-[var(--pc-controls-height-compact)] px-0",
      },
    },
    compoundVariants: [
      {
        variant: "text",
        className: "h-auto min-h-0 border-0 px-0 py-0",
      },
      {
        variant: "navigation",
        className:
          "h-[var(--pc-controls-height-nav)] min-h-[var(--pc-controls-height-nav)]",
      },
    ],
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  }) {
  const Comp = asChild ? Slot.Root : "button";

  return (
    <Comp
      data-slot="button"
      data-variant={variant ?? "default"}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

function ButtonGroup({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="button-group"
      role="group"
      className={cn(
        "inline-flex w-fit gap-1 rounded-md border border-border bg-muted p-1",
        className,
      )}
      {...props}
    />
  );
}

export { Button, ButtonGroup, buttonVariants };
