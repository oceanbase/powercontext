/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { CircleCheck, Power, Sparkles } from "lucide-react";
import type { Language } from "./messages";
import { PersistentGuide } from "@/components/ui/persistent-guide";

const GUIDE_SEQUENCE_DELAY_MS = 3 * 60 * 1000;
const STARTUP_GUIDE_RETRY_DELAY_MS = 24 * 60 * 60 * 1000;

const copy = {
  zh: {
    startupTitle: "开启本机 PowerContext 服务开机自动启动",
    startupBody:
      "登录系统后自动启动 PowerContext，本地 Agent 无需再次手动连接。",
    startupAction: "立即开启",
    modelTitle: "为本地服务配置模型能力",
    modelBody:
      "在本地 PowerContext 服务中配置生成与向量模型，让 Agent 可以整理、检索和使用你的记忆。",
    generationModelAction: "配置生成模型",
    embeddingModelAction: "配置嵌入模型",
    close: "关闭通知",
    startupSuccessTitle: "开机自动启动已开启",
    startupSuccessBody: "本机 PowerContext 服务将在登录系统后自动启动。",
  },
  en: {
    startupTitle: "Start PowerContext automatically",
    startupBody:
      "Launch PowerContext when you sign in so local Agents reconnect without manual setup.",
    startupAction: "Turn on",
    modelTitle: "Configure models for the local service",
    modelBody:
      "Set up generation and embedding models in your local PowerContext service so Agents can organize, search, and use your memories.",
    generationModelAction: "Configure generation model",
    embeddingModelAction: "Configure embedding model",
    close: "Close notification",
    startupSuccessTitle: "Automatic startup is on",
    startupSuccessBody:
      "The local PowerContext service will start after you sign in.",
  },
} as const;

export type ProgressiveGuideStep = "startup" | "models" | null;

export function hasAnyConfiguredModel(configured: {
  generation: boolean;
  embedding: boolean;
}) {
  return configured.generation || configured.embedding;
}

export function canShowStartupGuide({
  prerequisitesMet,
  startOnLogin,
  disabledByUser,
  dismissCount,
  dismissedAt,
  now = Date.now(),
}: {
  prerequisitesMet: boolean;
  startOnLogin: boolean;
  disabledByUser: boolean;
  dismissCount: number;
  dismissedAt: number | null;
  now?: number;
}) {
  if (!prerequisitesMet || startOnLogin || disabledByUser || dismissCount >= 2)
    return false;
  if (dismissCount === 0) return true;
  return (
    dismissedAt !== null && now - dismissedAt >= STARTUP_GUIDE_RETRY_DELAY_MS
  );
}

export function getProgressiveGuideStep({
  prerequisitesMet,
  startupGuideAllowed,
  anyModelConfigured,
  previousGuideEndedAt,
  now = Date.now(),
}: {
  prerequisitesMet: boolean;
  startupGuideAllowed: boolean;
  anyModelConfigured: boolean;
  previousGuideEndedAt: number | null;
  now?: number;
}): ProgressiveGuideStep {
  if (!prerequisitesMet) return null;
  if (startupGuideAllowed) return "startup";
  if (anyModelConfigured) return null;
  if (
    previousGuideEndedAt !== null &&
    now - previousGuideEndedAt < GUIDE_SEQUENCE_DELAY_MS
  )
    return null;
  return "models";
}

export function StartupSuccessNotification({
  language,
  onClose,
}: {
  language: Language;
  onClose: () => void;
}) {
  const t = copy[language];
  return (
    <PersistentGuide
      icon={<CircleCheck />}
      title={t.startupSuccessTitle}
      description={t.startupSuccessBody}
      closeLabel={t.close}
      onClose={onClose}
      tone="success"
    />
  );
}

export function ProgressiveSetupGuide({
  step,
  language,
  onAction,
  onSecondaryAction,
  onClose,
}: {
  step: Exclude<ProgressiveGuideStep, null>;
  language: Language;
  onAction: () => void;
  onSecondaryAction?: () => void;
  onClose: () => void;
}) {
  const t = copy[language];
  const startup = step === "startup";
  const Icon = startup ? Power : Sparkles;

  return (
    <PersistentGuide
      icon={<Icon />}
      title={startup ? t.startupTitle : t.modelTitle}
      description={startup ? t.startupBody : t.modelBody}
      action={startup ? t.startupAction : t.generationModelAction}
      onAction={onAction}
      secondaryAction={startup ? undefined : t.embeddingModelAction}
      onSecondaryAction={startup ? undefined : onSecondaryAction}
      closeLabel={t.close}
      onClose={onClose}
    />
  );
}

export { GUIDE_SEQUENCE_DELAY_MS, STARTUP_GUIDE_RETRY_DELAY_MS };
