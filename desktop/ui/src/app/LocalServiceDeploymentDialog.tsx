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

import { useEffect, useRef, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageFeedback } from "@/components/ui/feedback";
import {
  Dialog,
  DialogBody,
  DialogCloseButton,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { Language } from "./messages";

const RETRY_WINDOW_MS = 3 * 60 * 1000;
const RETRY_INTERVAL_MS = 1500;

const copy = {
  zh: {
    title: "安装 PowerContext 服务",
    description:
      "PowerContext 将安装到系统默认位置，并自动启动服务、创建默认记忆空间和建立连接。",
    cancel: "取消",
    deploy: "安装服务",
    deploying: "正在安装…",
    failed: "安装本机服务失败",
    failedHint: "已自动重试 3 分钟。请处理以下问题后重试。",
    retry: "重新安装",
    close: "关闭弹窗",
  },
  en: {
    title: "Install PowerContext service",
    description:
      "PowerContext will install in the system default location, start the service, create a default memory space, and connect it.",
    cancel: "Cancel",
    deploy: "Install service",
    deploying: "Installing…",
    failed: "Could not deploy the local service",
    failedHint:
      "Automatic retries continued for 3 minutes. Resolve the issue below, then try again.",
    retry: "Install again",
    close: "Close dialog",
  },
} as const;

export function LocalServiceDeploymentDialog({
  language,
  available,
  run,
  onClose,
  onComplete,
}: {
  language: Language;
  available: boolean;
  run: (signal: AbortSignal) => Promise<void>;
  onClose: () => void;
  onComplete: () => void;
}) {
  const t = copy[language];
  const [phase, setPhase] = useState<"idle" | "running" | "failed">("idle");
  const [reason, setReason] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const retryTimerRef = useRef<number | null>(null);

  function clearAttempt() {
    controllerRef.current?.abort();
    controllerRef.current = null;
    if (retryTimerRef.current !== null) {
      window.clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
  }

  useEffect(() => clearAttempt, []);

  function close() {
    clearAttempt();
    onClose();
  }

  function start() {
    if (!available) return;
    clearAttempt();
    const controller = new AbortController();
    controllerRef.current = controller;
    const deadline = Date.now() + RETRY_WINDOW_MS;
    setPhase("running");
    setReason("");

    const attempt = async () => {
      try {
        await run(controller.signal);
        if (controller.signal.aborted) return;
        clearAttempt();
        onComplete();
      } catch (error) {
        if (controller.signal.aborted) return;
        const message =
          error instanceof Error && error.message.trim()
            ? error.message
            : language === "zh"
              ? "无法完成本机服务安装。"
              : "The local service installation could not be completed.";
        if (Date.now() < deadline) {
          retryTimerRef.current = window.setTimeout(
            () => void attempt(),
            RETRY_INTERVAL_MS,
          );
          return;
        }
        controllerRef.current = null;
        setReason(message);
        setPhase("failed");
      }
    };

    void attempt();
  }

  return (
    <Dialog open onOpenChange={(open) => !open && close()}>
      <DialogContent aria-label={t.title}>
        <DialogHeader>
          <DialogTitle>{t.title}</DialogTitle>
          <DialogCloseButton label={t.close} />
        </DialogHeader>
        <DialogBody className="local-deployment-content">
          {!available ? (
            <p>
              {language === "zh"
                ? "当前版本暂不支持从 Desktop 安装本机服务。"
                : "Installing a local service from Desktop is unavailable in this build."}
            </p>
          ) : phase === "failed" ? (
            <PageFeedback compact kind="error" title={t.failed}>
              <p>{t.failedHint}</p>
              <p>{reason}</p>
            </PageFeedback>
          ) : (
            <p>{t.description}</p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={close}>
            {t.cancel}
          </Button>
          <Button disabled={!available || phase === "running"} onClick={start}>
            {phase === "running" && (
              <LoaderCircle
                className="motion-safe:animate-spin motion-reduce:animate-none"
                aria-hidden="true"
              />
            )}
            {phase === "running"
              ? t.deploying
              : phase === "failed"
                ? t.retry
                : t.deploy}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
