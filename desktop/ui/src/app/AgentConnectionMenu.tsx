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

import { useRef, useState } from "react";
import { CircleCheck, Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
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
import { useConfirmation } from "./useConfirmation";

const copy = {
  zh: {
    manage: "管理连接",
    changeBinding: "更改服务与空间",
    captureSettings: "消息采集设置",
    verify: "验证连接",
    remove: "删除连接",
    captureTitle: "消息采集设置",
    captureLabel: "采集用户消息",
    captureHint:
      "保存用户提交的消息及其中粘贴的文本或代码，不采集 Agent 回复和工具输出。",
    cancel: "取消",
    save: "保存",
    verifyTitle: "验证连接",
    verifying: "正在检查安装、配置加载和实际读写…",
    verified: "连接验证通过",
    verifiedHint: "Agent 已加载当前配置，读取和保存均可正常使用。",
    issueFound: "发现连接问题",
    simulatedProblem: "Agent 尚未加载最新的 PowerContext 配置。",
    simulatedSuggestion: "请重新加载 Agent 配置，然后再次验证连接。",
    suggestion: "建议处理",
    close: "完成",
    deleteTitle: "确认删除连接",
    deleteMessage:
      "删除后，此 Agent 将停止使用当前 PowerContext 连接。已有记忆不会被删除。",
    delete: "删除",
  },
  en: {
    manage: "Manage connection",
    changeBinding: "Change service and space",
    captureSettings: "Message capture settings",
    verify: "Verify connection",
    remove: "Delete connection",
    captureTitle: "Message capture settings",
    captureLabel: "Capture user messages",
    captureHint:
      "Save user submissions and pasted text or code. Agent replies and tool output are not captured.",
    cancel: "Cancel",
    save: "Save",
    verifyTitle: "Verify connection",
    verifying:
      "Checking installation, configuration loading, and actual reads and writes…",
    verified: "Connection verified",
    verifiedHint:
      "The Agent loaded the current configuration and can read and save normally.",
    issueFound: "Connection issue found",
    simulatedProblem:
      "The Agent has not loaded the latest PowerContext configuration.",
    simulatedSuggestion:
      "Reload the Agent configuration, then verify the connection again.",
    suggestion: "Suggested fix",
    close: "Done",
    deleteTitle: "Delete connection?",
    deleteMessage:
      "This Agent will stop using the current PowerContext connection. Existing memories will not be deleted.",
    delete: "Delete",
  },
} as const;

export function AgentConnectionMenu({
  agent,
  language,
  onChangeBinding,
  onDelete,
  onDeleted,
  verificationIssue,
  disabled = false,
  disabledReason,
  onCaptureChange,
  onCaptureSaved,
}: {
  agent: string;
  language: Language;
  onChangeBinding: () => void;
  onDelete: () => void;
  onDeleted?: () => void;
  verificationIssue?: { problem: string; suggestion: string };
  disabled?: boolean;
  disabledReason?: string;
  onCaptureChange?: (enabled: boolean) => void;
  onCaptureSaved?: (enabled: boolean) => void;
}) {
  const t = copy[language];
  const { confirm, confirmation } = useConfirmation(language);
  const [captureOpen, setCaptureOpen] = useState(false);
  const [capturePrompts, setCapturePrompts] = useState(true);
  const [draftCapturePrompts, setDraftCapturePrompts] = useState(true);
  const [verification, setVerification] = useState<
    "idle" | "checking" | "passed" | "failed"
  >("idle");
  const verificationAttempts = useRef(0);
  const displayedVerificationIssue = verificationIssue ?? {
    problem: t.simulatedProblem,
    suggestion: t.simulatedSuggestion,
  };

  function verify() {
    setVerification("checking");
    const firstAttempt = verificationAttempts.current++ === 0;
    window.setTimeout(
      () => setVerification(firstAttempt ? "failed" : "passed"),
      450,
    );
  }

  async function remove() {
    const accepted = await confirm({
      title: t.deleteTitle,
      message: t.deleteMessage,
      confirmLabel: t.delete,
      destructive: true,
      primaryMessage: true,
    });
    if (accepted) {
      onDelete();
      onDeleted?.();
    }
  }

  return (
    <>
      {confirmation}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <span title={disabled ? disabledReason : t.manage}>
            <Button
              size="icon-sm"
              variant="outline"
              aria-label={`${t.manage}：${agent}`}
              disabled={disabled}
            >
              <Settings aria-hidden="true" />
            </Button>
          </span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="agent-connection-menu">
          <DropdownMenuItem onSelect={onChangeBinding}>
            {t.changeBinding}
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              setDraftCapturePrompts(capturePrompts);
              setCaptureOpen(true);
            }}
          >
            {t.captureSettings}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={verify}>{t.verify}</DropdownMenuItem>
          <DropdownMenuSeparator className="service-menu-separator" />
          <DropdownMenuItem destructive onSelect={() => void remove()}>
            {t.remove}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {captureOpen && (
        <Dialog open onOpenChange={(open) => !open && setCaptureOpen(false)}>
          <DialogContent>
            <DialogHeader className="agent-setting-heading">
              <DialogTitle>{t.captureTitle}</DialogTitle>
              <DialogCloseButton label={t.cancel} />
            </DialogHeader>
            <DialogBody className="agent-setting-dialog">
              <div className="agent-setting-row">
                <div>
                  <strong>{t.captureLabel}</strong>
                  <p>{t.captureHint}</p>
                </div>
                <Switch
                  checked={draftCapturePrompts}
                  onCheckedChange={setDraftCapturePrompts}
                  aria-label={t.captureLabel}
                />
              </div>
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={() => setCaptureOpen(false)}>
                {t.cancel}
              </Button>
              <Button
                onClick={() => {
                  setCapturePrompts(draftCapturePrompts);
                  onCaptureChange?.(draftCapturePrompts);
                  setCaptureOpen(false);
                  onCaptureSaved?.(draftCapturePrompts);
                }}
              >
                {t.save}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}

      {verification !== "idle" && (
        <Dialog open onOpenChange={(open) => !open && setVerification("idle")}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t.verifyTitle}</DialogTitle>
            </DialogHeader>
            <DialogBody
              className="agent-verification-result"
              aria-live="polite"
            >
              {verification === "checking" ? (
                <p>{t.verifying}</p>
              ) : verification === "failed" ? (
                <>
                  <div className="agent-verification-block">
                    <strong className="agent-verification-error">
                      {t.issueFound}
                    </strong>
                    <p>{displayedVerificationIssue.problem}</p>
                  </div>
                  <div className="agent-verification-block">
                    <strong className="agent-verification-suggestion">
                      {t.suggestion}
                    </strong>
                    <p>{displayedVerificationIssue.suggestion}</p>
                  </div>
                </>
              ) : (
                <div className="agent-verification-block">
                  <strong className="agent-verification-success">
                    <CircleCheck aria-hidden="true" />
                    {t.verified}
                  </strong>
                  <p>{t.verifiedHint}</p>
                </div>
              )}
            </DialogBody>
            <DialogFooter>
              <Button
                variant="outline"
                disabled={verification === "checking"}
                onClick={() => setVerification("idle")}
              >
                {t.close}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}
