import * as React from "react";
import { Check, ChevronDown } from "lucide-react";
import { Select as SelectPrimitive } from "radix-ui";
import { cn } from "../../lib/utils";
import { fieldStateClasses } from "./field";

export const Select = SelectPrimitive.Root;
export const SelectValue = SelectPrimitive.Value;
export const SelectGroup = SelectPrimitive.Group;

export function SelectLabel({
  className,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Label>) {
  return (
    <SelectPrimitive.Label
      data-slot="select-label"
      className={cn(
        "px-2.5 py-1.5 text-[length:var(--pc-text-caption)] font-medium text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

export function SelectTrigger({
  className,
  children,
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      className={cn(
        "pc-select-trigger inline-flex h-[var(--pc-controls-height-field)] min-h-[var(--pc-controls-height-field)] min-w-[104px] items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-1.5 text-[length:var(--pc-text-dropdown)] font-normal leading-[var(--pc-leading-action)] text-foreground data-[state=open]:border-primary aria-invalid:data-[state=open]:border-destructive data-[placeholder]:text-[var(--text-tertiary)] [&_svg]:size-3.5 [&_svg]:shrink-0 [&_svg]:text-muted-foreground",
        fieldStateClasses,
        className,
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <ChevronDown aria-hidden="true" />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

export function SelectContent({
  className,
  children,
  empty = false,
  position = "popper",
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Content> & { empty?: boolean }) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        data-slot="select-content"
        className={cn(
          "pc-select-content z-[var(--pc-z-index-modal-popover)] min-w-[var(--radix-select-trigger-width)] overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-[var(--pc-shadows-floating)]",
          className,
        )}
        position={position}
        sideOffset={4}
        {...props}
      >
        <SelectPrimitive.Viewport
          className={cn(
            "pc-select-viewport p-1",
            empty &&
              "grid min-h-[calc(var(--pc-controls-height-compact)*4)] place-items-center",
          )}
        >
          {children}
        </SelectPrimitive.Viewport>
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

export function SelectItem({
  className,
  children,
  variant = "default",
  ...props
}: React.ComponentProps<typeof SelectPrimitive.Item> & {
  variant?: "default" | "empty";
}) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        "pc-select-item relative flex min-h-[34px] cursor-default select-none items-center rounded-sm py-1.5 pr-8 pl-2.5 text-[length:var(--pc-text-dropdown)] font-normal leading-[var(--pc-leading-dropdown)] outline-none data-[disabled]:pointer-events-none data-[disabled]:opacity-50 data-[highlighted]:bg-accent",
        variant === "empty" &&
          "min-h-0 w-full justify-center px-3 py-2 text-center text-[length:var(--pc-text-caption)] text-muted-foreground data-[disabled]:opacity-100",
        className,
      )}
      {...props}
      disabled={variant === "empty" || props.disabled}
    >
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
      <SelectPrimitive.ItemIndicator className="pc-select-indicator absolute right-2.5 grid place-items-center [&_svg]:size-3.5">
        <Check aria-hidden="true" />
      </SelectPrimitive.ItemIndicator>
    </SelectPrimitive.Item>
  );
}
