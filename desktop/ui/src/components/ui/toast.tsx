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

import type { ComponentProps } from "react";
import { Toast as ToastPrimitive } from "radix-ui";
import { cn } from "@/lib/utils";

export const ToastProvider = ToastPrimitive.Provider;
export const ToastTitle = ToastPrimitive.Title;
export const ToastDescription = ToastPrimitive.Description;
export const ToastClose = ToastPrimitive.Close;
export const ToastAction = ToastPrimitive.Action;

export function Toast({
  className,
  ...props
}: ComponentProps<typeof ToastPrimitive.Root>) {
  return (
    <ToastPrimitive.Root
      data-slot="toast"
      className={cn(
        "relative grid w-full grid-cols-[36px_minmax(0,1fr)] items-start gap-3 overflow-hidden rounded-lg border border-border bg-card p-6 text-card-foreground shadow-[var(--pc-shadows-floating)] data-[state=open]:animate-in data-[state=open]:fade-in data-[state=open]:slide-in-from-bottom-2 duration-200 motion-reduce:animate-none",
        className,
      )}
      {...props}
    />
  );
}

export function ToastViewport({
  className,
  ...props
}: ComponentProps<typeof ToastPrimitive.Viewport>) {
  return (
    <ToastPrimitive.Viewport
      data-slot="toast-viewport"
      className={cn(
        "fixed right-4 bottom-4 z-[var(--pc-z-index-toast)] m-0 flex w-[360px] max-w-[calc(100vw-32px)] list-none flex-col gap-3 p-0 outline-none max-[680px]:left-4 max-[680px]:w-auto max-[680px]:max-w-none",
        className,
      )}
      {...props}
    />
  );
}
