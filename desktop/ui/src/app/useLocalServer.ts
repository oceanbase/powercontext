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
import { opsApi, subscribeOpsProgress } from "../shared/ops";
import type {
  OpsInspectionReply,
  OpsReferences,
  OpsOperationReply,
  OpsProgress,
  OpsPreflightInput,
  OpsPlanApproval,
} from "../generated/ipc";
import { localError } from "./local-server-messages";

type Approved = { plan: OpsPlanApproval; input: OpsPreflightInput };
export function useLocalServer() {
  const [inspection, setInspection] = useState<OpsInspectionReply | null>(null);
  const [references, setReferences] = useState<OpsReferences | null>(null);
  const [selected, setSelected] = useState("");
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [record, setRecord] = useState<OpsOperationReply | null>(null);
  const [progress, setProgress] = useState<OpsProgress | null>(null);
  const [approval, setApprovalState] = useState<Approved | null>(null);
  const approvalRef = useRef<Approved | null>(null);
  function setApproval(next: Approved | null) {
    approvalRef.current = next;
    setApprovalState(next);
  }
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [querying, setQuerying] = useState(false);
  const [controlling, setControlling] = useState(false);
  const [busy, setBusy] = useState<"preflight" | "execute" | null>(null);
  const [listening, setListening] = useState(false);
  const mounted = useRef(false);
  const mutation = useRef(false);
  const reads = useRef(0);
  const queries = useRef(0);
  const running = useRef<{
    approvalId: string;
    knownIds: Set<string>;
    operationId?: string;
    attempt?: number;
  } | null>(null);

  function choose(id: string) {
    queries.current++;
    selectedRef.current = id;
    setSelected(id);
    setRecord(null);
    setApproval(null);
  }
  async function refresh(clearError = true) {
    const request = ++reads.current;
    setLoading(true);
    if (clearError) setError(null);
    const [facts, refs] = await Promise.allSettled([
      Promise.resolve().then(() => opsApi.inspect()),
      Promise.resolve().then(() => opsApi.references()),
    ]);
    if (!mounted.current || request !== reads.current) return;
    setInspection(facts.status === "fulfilled" ? facts.value : null);
    setReferences(refs.status === "fulfilled" ? refs.value : null);
    if (facts.status === "rejected" || refs.status === "rejected") {
      const failure =
        facts.status === "rejected"
          ? facts.reason
          : refs.status === "rejected"
            ? refs.reason
            : null;
      setError((current) =>
        !clearError && current ? current : localError(failure),
      );
    }
    const active = running.current;
    if (refs.status === "fulfilled" && active && !active.operationId) {
      const added = refs.value.operations.filter(
        (item) => !active.knownIds.has(item.operationId),
      );
      if (added.length === 1 && added[0].bindingStatus === "current") {
        active.operationId = added[0].operationId;
        choose(added[0].operationId);
      }
    }
    if (
      refs.status === "fulfilled" &&
      !selectedRef.current &&
      refs.value.operations.length
    ) {
      choose(
        refs.value.operations[refs.value.operations.length - 1].operationId,
      );
    }
    setLoading(false);
    return refs.status === "fulfilled" ? refs.value : null;
  }
  function receive(reply: OpsOperationReply) {
    if (!mounted.current || reply.operationId !== selectedRef.current) return;
    const active = running.current;
    if (
      active?.operationId === reply.operationId &&
      active.attempt &&
      (reply.attempt ?? 0) < active.attempt
    )
      return;
    setRecord((current) => {
      if (current && (current.attempt ?? 0) > (reply.attempt ?? 0))
        return current;
      if (
        current?.attempt === reply.attempt &&
        current?.status?.result &&
        !reply.status?.result
      )
        return current;
      return reply;
    });
  }
  async function query(id = selectedRef.current) {
    if (!id || !mounted.current) return;
    const request = ++queries.current;
    setQuerying(true);
    try {
      const reply = await opsApi.operationStatus({ operationId: id });
      if (request === queries.current) receive(reply);
    } catch (failure) {
      if (mounted.current && request === queries.current)
        setError(localError(failure));
    } finally {
      if (mounted.current && request === queries.current) setQuerying(false);
    }
  }
  useEffect(() => {
    mounted.current = true;
    void refresh();
    let unlisten: (() => void) | undefined;
    let disposed = false;
    void Promise.resolve()
      .then(() =>
        subscribeOpsProgress((event) => {
          const active = running.current;
          if (
            disposed ||
            !mounted.current ||
            !active ||
            event.approvalId !== active.approvalId
          )
            return;
          if (active.operationId && active.operationId !== event.operationId)
            return;
          if (active.attempt && active.attempt !== event.attempt) return;
          active.operationId = event.operationId;
          active.attempt = event.attempt;
          selectedRef.current = event.operationId;
          setSelected(event.operationId);
          setProgress((current) =>
            current &&
            current.operationId === event.operationId &&
            (current.sequence ?? -1) > (event.sequence ?? -1)
              ? current
              : event,
          );
        }),
      )
      .then((stop) => {
        if (!disposed && mounted.current) {
          unlisten = stop;
          setListening(true);
        } else stop();
      })
      .catch(() => {
        if (!disposed && mounted.current) setListening(false);
      });
    return () => {
      disposed = true;
      mounted.current = false;
      reads.current++;
      queries.current++;
      unlisten?.();
    };
  }, []);
  useEffect(() => {
    if (selected) void query(selected);
  }, [selected]);

  async function preflight(input: OpsPreflightInput) {
    if (mutation.current) return;
    mutation.current = true;
    setBusy("preflight");
    setError(null);
    setApproval(null);
    try {
      const reply = await opsApi.preflight(input);
      if (!mounted.current) return;
      if (reply.outcome === "succeeded" && reply.approval)
        setApproval({ plan: reply.approval, input });
      else setError(localError(reply.error ?? reply.bridgeError));
    } catch (failure) {
      if (mounted.current) setError(localError(failure));
    } finally {
      mutation.current = false;
      if (mounted.current) setBusy(null);
    }
  }
  async function execute(expectedApprovalId: string) {
    const accepted = approvalRef.current;
    if (
      !mounted.current ||
      mutation.current ||
      !accepted ||
      accepted.plan.approvalId !== expectedApprovalId
    )
      return;
    mutation.current = true;
    const knownIds = new Set(
      references?.operations.map((item) => item.operationId),
    );
    setApproval(null);
    setBusy("execute");
    setError(null);
    setProgress(null);
    setRecord(null);
    running.current = {
      approvalId: accepted.plan.approvalId,
      knownIds,
      operationId: accepted.input.resume?.operationId,
      attempt: accepted.input.resume
        ? accepted.input.resume.expectedAttempt + 1
        : undefined,
    };
    try {
      const input = {
        approvalId: accepted.plan.approvalId,
        planId: accepted.plan.planId,
      };
      const reply = await (accepted.input.resume
        ? opsApi.resume(input)
        : opsApi.apply(input));
      if (!mounted.current) return;
      queries.current++;
      selectedRef.current = reply.operationId;
      setSelected(reply.operationId);
      receive(reply);
    } catch (failure) {
      if (mounted.current) setError(localError(failure));
    } finally {
      const observedId = running.current?.operationId;
      running.current = null;
      if (mounted.current) {
        // Discover a newly persisted reference even if neither progress nor the reply arrived.
        const latest = await refresh(false);
        const added =
          latest?.operations.filter(
            (item) => !knownIds.has(item.operationId),
          ) ?? [];
        const id =
          observedId ?? (added.length === 1 ? added[0].operationId : undefined);
        if (id && id !== selectedRef.current) choose(id);
        if (mounted.current) await query();
        if (mounted.current) setBusy(null);
      }
      mutation.current = false;
    }
  }
  async function cancel() {
    const operationId = running.current?.operationId ?? record?.operationId;
    const attempt = running.current?.attempt ?? record?.attempt;
    if (!operationId || !attempt || controlling) return;
    setControlling(true);
    try {
      receive(await opsApi.cancel({ operationId, attempt }));
    } catch (failure) {
      if (mounted.current) setError(localError(failure));
    } finally {
      if (mounted.current) setControlling(false);
    }
  }
  const reference = references?.operations.find(
    (item) => item.operationId === selected,
  );
  const status = record?.status;
  const canResume =
    !!reference?.operation &&
    !!reference.options &&
    reference.bindingStatus === "current" &&
    !!status &&
    status.outcome !== "succeeded" &&
    status.recoveryActions.includes("preflight") &&
    (status.executionState === "interrupted" ||
      status.executionState === "finished");
  return {
    inspection,
    references,
    selected,
    reference,
    record,
    progress,
    approval,
    error,
    loading,
    querying,
    controlling,
    busy,
    listening,
    canResume,
    refresh,
    choose,
    query,
    preflight,
    execute,
    cancel,
    dismiss: () => setApproval(null),
  };
}
export type LocalServerController = ReturnType<typeof useLocalServer>;
