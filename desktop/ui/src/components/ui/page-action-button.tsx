/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { Plus } from "lucide-react";
import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

function PageActionButton({
  className,
  children,
  ...props
}: Omit<ComponentProps<typeof Button>, "variant" | "size">) {
  return (
    <Button
      variant="outline"
      size="sm"
      className={cn("gap-1.5 px-3 font-normal [&_svg]:size-3.5", className)}
      {...props}
    >
      <Plus aria-hidden="true" />
      {children}
    </Button>
  );
}

export { PageActionButton };
