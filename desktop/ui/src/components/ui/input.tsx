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
