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
import { Button } from "@/components/ui/button";
import { PageFeedback } from "@/components/ui/feedback";
import { Textarea } from "@/components/ui/textarea";
import type { MemoryContext, WriteRecord } from "../generated/ipc";
import type { Language } from "./messages";

type Draft = {
  id: number;
  text: string;
  target: MemoryContext;
  serviceName: string;
  scopeTitle: string;
  phase: "pending" | "unknown" | "failed" | "unconfirmed";
  acknowledged: boolean;
};

export function useMemorySubmissions() {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const sequence = useRef(0);

  function begin(
    text: string,
    target: MemoryContext,
    serviceName: string,
    scopeTitle: string,
  ) {
    const id = ++sequence.current;
    setDrafts((current) => [
      ...current,
      {
        id,
        text,
        target,
        serviceName,
        scopeTitle,
        phase: "pending",
        acknowledged: false,
      },
    ]);
    return (record: WriteRecord | null, dispatched?: boolean) => {
      // A late receipt can only update the draft that started this write.
      setDrafts((current) =>
        current.flatMap((draft) => {
          if (draft.id !== id) return [draft];
          if (record?.status === "succeeded") return [];
          return [
            {
              ...draft,
              target: record?.context ?? draft.target,
              phase:
                record?.status ??
                (dispatched === false ? "failed" : "unconfirmed"),
            },
          ];
        }),
      );
    };
  }

  function acknowledge(id: number) {
    setDrafts((current) =>
      current.map((draft) =>
        draft.id === id ? { ...draft, acknowledged: true } : draft,
      ),
    );
  }

  function discard(id: number) {
    setDrafts((current) => current.filter((draft) => draft.id !== id));
  }

  return { drafts, begin, acknowledge, discard };
}

export type MemorySubmissions = ReturnType<typeof useMemorySubmissions>;

export function MemoryWriteRecovery({
  submissions,
  language,
}: {
  submissions: MemorySubmissions;
  language: Language;
}) {
  const zh = language === "zh";
  const label = zh ? "恢复记忆提交" : "Recover a memory submission";
  return submissions.drafts.map((draft) => {
    const pending = draft.phase === "pending";
    const failed = draft.phase === "failed";
    const requiresCheck = !pending && !failed && !draft.acknowledged;
    return (
      <section key={draft.id} aria-label={label}>
        <PageFeedback
          compact
          kind={pending ? "loading" : failed ? "error" : "warning"}
          title={
            pending
              ? zh
                ? "正在保存记忆"
                : "Saving memory"
              : failed
                ? zh
                  ? "记忆未保存"
                  : "Memory was not saved"
                : zh
                  ? "记忆保存结果未知"
                  : "Memory save outcome is unknown"
          }
          action={
            requiresCheck ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => submissions.acknowledge(draft.id)}
              >
                {zh
                  ? "已核对，允许再次提交"
                  : "I checked. Allow another submission"}
              </Button>
            ) : !pending ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => submissions.discard(draft.id)}
              >
                {zh ? "丢弃恢复副本" : "Discard recovery copy"}
              </Button>
            ) : undefined
          }
        >
          <p>
            {draft.serviceName} · {draft.target.endpoint} · {draft.scopeTitle} (
            {draft.target.scopeId})
          </p>
          {(draft.target.serverId || draft.target.principal) && (
            <p>
              {[draft.target.serverId, draft.target.principal?.id]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
          {requiresCheck && (
            <p>
              {zh
                ? "再次提交前，请在原服务和空间中搜索这条记忆。"
                : "Search for this memory in its original service and space before submitting it again."}
            </p>
          )}
          <Textarea rows={2} aria-label={label} value={draft.text} readOnly />
        </PageFeedback>
      </section>
    );
  });
}
