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
import { ChevronDown } from "lucide-react";
import { Accordion as AccordionPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

const Accordion = AccordionPrimitive.Root;

function AccordionItem({
  className,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Item>) {
  return (
    <AccordionPrimitive.Item
      data-slot="accordion-item"
      className={cn("border-b border-border last:border-b-0", className)}
      {...props}
    />
  );
}

function AccordionTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Trigger>) {
  return (
    <AccordionPrimitive.Header className="m-0 flex">
      <AccordionPrimitive.Trigger
        data-slot="accordion-trigger"
        className={cn(
          "group flex w-full items-center text-left outline-none transition-colors duration-150 ease-out focus-visible:ring-[3px] focus-visible:ring-ring focus-visible:ring-inset disabled:pointer-events-none disabled:opacity-50",
          className,
        )}
        {...props}
      >
        <ChevronDown
          aria-hidden="true"
          className="pc-accordion-chevron size-4 shrink-0 -rotate-90 text-muted-foreground transition-transform duration-180 ease-out group-data-[state=open]:rotate-0"
        />
        {children}
      </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
  );
}

function AccordionIconTrigger({
  className,
  ...props
}: Omit<React.ComponentProps<typeof AccordionPrimitive.Trigger>, "children">) {
  return (
    <AccordionPrimitive.Header className="m-0 contents">
      <AccordionPrimitive.Trigger
        data-slot="accordion-icon-trigger"
        className={cn(
          "group inline-grid size-8 shrink-0 place-items-center rounded-sm border-0 bg-transparent outline-none transition-colors duration-150 ease-out hover:bg-muted focus-visible:ring-[3px] focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
          className,
        )}
        {...props}
      >
        <ChevronDown
          aria-hidden="true"
          className="size-4 -rotate-90 text-muted-foreground transition-transform duration-180 ease-out group-data-[state=open]:rotate-0"
        />
      </AccordionPrimitive.Trigger>
    </AccordionPrimitive.Header>
  );
}

function AccordionContent({
  className,
  children,
  ...props
}: React.ComponentProps<typeof AccordionPrimitive.Content>) {
  return (
    <AccordionPrimitive.Content
      data-slot="accordion-content"
      className="overflow-hidden data-[state=closed]:hidden"
      {...props}
    >
      <div className={cn(className)}>{children}</div>
    </AccordionPrimitive.Content>
  );
}

export {
  Accordion,
  AccordionContent,
  AccordionIconTrigger,
  AccordionItem,
  AccordionTrigger,
};
