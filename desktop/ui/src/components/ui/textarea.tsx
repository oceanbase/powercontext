import * as React from "react";

import { cn } from "@/lib/utils";
import { fieldStateClasses, readOnlyFieldClasses } from "./field";

function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "flex min-h-24 w-full min-w-0 resize-y rounded-md border border-input bg-background px-3 py-2 text-[length:var(--pc-text-action)] font-normal leading-[var(--pc-leading-action)] shadow-none",
        fieldStateClasses,
        readOnlyFieldClasses,
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
