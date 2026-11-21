import * as React from "react";

import { cn } from "@/lib/utils";
import { fieldStateClasses, readOnlyFieldClasses } from "./field";

function Input({
  className,
  type,
  adornment,
  ...props
}: React.ComponentProps<"input"> & {
  adornment?: "leading" | "trailing" | "both";
}) {
  return (
    <input
      data-slot="input"
      type={type}
      className={cn(
        "flex h-[var(--pc-controls-height-field)] min-h-[var(--pc-controls-height-field)] w-full min-w-0 rounded-md border border-input bg-background px-3 py-1.5 text-[length:var(--pc-text-action)] font-normal leading-[var(--pc-leading-action)] shadow-none",
        fieldStateClasses,
        readOnlyFieldClasses,
        (adornment === "leading" || adornment === "both") && "pl-9",
        (adornment === "trailing" || adornment === "both") && "pr-10",
        className,
      )}
      {...props}
    />
  );
}

function InputIcon({ className, ...props }: React.ComponentProps<"span">) {
  return (
    <span
      aria-hidden="true"
      data-slot="input-icon"
      className={cn(
        "pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground [&_svg]:size-[var(--pc-controls-icon-md)] [&_svg]:[stroke-width:var(--pc-controls-icon-stroke)]",
        className,
      )}
      {...props}
    />
  );
}

export { Input, InputIcon };
