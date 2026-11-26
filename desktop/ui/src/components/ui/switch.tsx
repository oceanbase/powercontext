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
import { Switch as SwitchPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

function Switch({
  className,
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      className={cn(
        "group relative inline-flex h-8 min-h-8 w-10 min-w-10 shrink-0 cursor-pointer items-center rounded-full border-0 bg-transparent p-0 outline-none focus-visible:ring-[3px] focus-visible:ring-ring focus-visible:ring-offset-[3px] focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute top-1.5 left-0.5 h-5 w-9 rounded-full bg-muted shadow-[inset_0_0_0_1px_var(--border)] transition-colors group-hover:bg-accent group-data-[state=checked]:bg-primary group-data-[state=checked]:shadow-none group-data-[state=checked]:group-hover:bg-[var(--accent-hover)]"
      />
      <SwitchPrimitive.Thumb
        data-slot="switch-thumb"
        className="pointer-events-none absolute top-2 left-0.5 block size-4 rounded-full bg-[var(--switch-thumb)] shadow-[0_1px_2px_rgb(0_0_0_/_0.2)] transition-transform data-[state=checked]:translate-x-4.5 data-[state=checked]:bg-[var(--switch-thumb-checked)]"
      />
    </SwitchPrimitive.Root>
  );
}

export { Switch };
