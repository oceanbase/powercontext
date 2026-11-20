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

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { messages, type Language } from "./messages";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogCloseButton,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type ConfirmationOptions = {
  message: string;
  title?: string;
  confirmLabel?: string;
  destructive?: boolean;
  primaryMessage?: boolean;
};

export function useConfirmation(language: Language) {
  const [request, setRequest] = useState<{
    options: ConfirmationOptions;
    id: number;
  } | null>(null);
  const sequence = useRef(0);
  const descriptionId = useId();
  const pending = useRef<((answer: boolean) => void) | null>(null);
  const confirm = useCallback(
    (input: string | ConfirmationOptions): Promise<boolean> => {
      if (pending.current) return Promise.resolve(false);
      return new Promise((resolve) => {
        pending.current = resolve;
        setRequest({
          options: typeof input === "string" ? { message: input } : input,
          id: ++sequence.current,
        });
      });
    },
    [],
  );
  useEffect(
    () => () => {
      pending.current?.(false);
      pending.current = null;
    },
    [],
  );
  function answer(value: boolean) {
    const resolve = pending.current;
    pending.current = null;
    setRequest(null);
    resolve?.(value);
  }
  const t = messages[language];
  const options = request?.options;
  const title = options?.title ?? t.confirmTitle;
  const confirmation =
    request === null ? null : (
      <Dialog
        key={request.id}
        open
        onOpenChange={(open) => !open && answer(false)}
      >
        <DialogContent role="alertdialog" aria-describedby={descriptionId}>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogCloseButton label={t.closeDialog} />
          </DialogHeader>
          <DialogBody className="confirmation-dialog-content">
            <DialogDescription
              id={descriptionId}
              className={
                request.options.primaryMessage
                  ? "confirmation-primary-message"
                  : undefined
              }
            >
              {request.options.message}
            </DialogDescription>
          </DialogBody>
          <DialogFooter>
            <Button size="sm" variant="outline" onClick={() => answer(false)}>
              {t.cancelAction}
            </Button>
            <Button
              size="sm"
              variant={request.options.destructive ? "destructive" : "default"}
              onClick={() => answer(true)}
            >
              {request.options.confirmLabel ?? t.confirmAction}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  return { confirm, confirmation };
}
