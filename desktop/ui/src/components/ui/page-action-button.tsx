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
