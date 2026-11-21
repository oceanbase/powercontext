/*
Copyright (c) 2026 OceanBase.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/
import { useInfiniteQuery, useMutation, useQueries, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { service } from "./services";
import type { KB, Target, Edit } from "./types";
export const keys = {
  processing: ["processing-requests"],
  documentChanges: (scope: string) => ["document-changes", scope],
  refresh: ["manual-refresh"],
  spaces: ["spaces"],
  defaults: ["default-space"],
  libraries: (space: string) => ["libraries", space],
  documents: (scope: string) => ["documents", scope],
  head: (scope: string, id: string) => ["head", scope, id],
  governance: (scope: string, id: string) => ["governance", scope, id],
  revisions: (scope: string, id: string) => ["revisions", scope, id],
};
export function useDocumentChanges(scopes: string[]) {
  return useQueries({
    queries: [keys.refresh, ...scopes.map(keys.documentChanges)].map((queryKey) => ({
      queryKey, initialData: 0, enabled: false, gcTime: Infinity,
    })),
  }).map((query) => query.data);
}
export function markDocumentChange(client: QueryClient, scope: string) {
  client.setQueryData<number>(keys.documentChanges(scope), (value) => (value ?? 0) + 1);
}
export const useSpaces = () => useQuery({ queryKey: keys.spaces, queryFn: service.spaces });
export const useDefault = () => useQuery({ queryKey: keys.defaults, queryFn: service.defaultSpace });
export const useHostContext = () =>
  useQuery({ queryKey: ["host-context"], queryFn: service.context, staleTime: Infinity });
export const useLibrary = (scope: string | null | undefined) =>
  useQuery({
    queryKey: ["host-library", scope],
    queryFn: () => service.library(scope!),
    enabled: !!scope,
    retry: false,
  });
export const useLibraries = (space: string) =>
  useQuery({ queryKey: keys.libraries(space), queryFn: () => service.libraries(space), enabled: !!space });
export const useDocuments = (scope: string) =>
  useInfiniteQuery({
    queryKey: keys.documents(scope),
    queryFn: ({ pageParam }) => service.documents(scope, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: (query) =>
      query.state.data?.pages[0]?.requests.some((item) => ["pending", "processing"].includes(item.state))
        ? 5000
        : false,
  });
export const useHead = (scope: string, id: string) =>
  useQuery({ queryKey: keys.head(scope, id), queryFn: () => service.head(scope, id) });
export const useGovernance = (scope: string, id: string) =>
  useQuery({ queryKey: keys.governance(scope, id), queryFn: () => service.governance(scope, id) });
export const useRevisions = (scope: string, id: string) =>
  useInfiniteQuery({
    queryKey: keys.revisions(scope, id),
    queryFn: ({ pageParam }) => service.revisions(scope, id, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
export const useChunks = (scope: string, id: string, revision: number) =>
  useInfiniteQuery({
    queryKey: ["chunks", scope, id, revision],
    queryFn: ({ pageParam }) => service.chunks(scope, id, revision, pageParam),
    initialPageParam: 0,
    getNextPageParam: (page) => page.next_offset ?? undefined,
    enabled: revision > 0,
  });
export const useChunk = (scope: string, id: string, revision: number, chunk: string) =>
  useQuery({
    queryKey: ["chunk", scope, id, revision, chunk],
    queryFn: () => service.chunk(scope, id, revision, chunk),
    enabled: revision > 0 && !!chunk,
  });
export function useRefresh() {
  const client = useQueryClient();
  return () => {
    client.setQueryData<number>(keys.refresh, (previous) => (previous ?? 0) + 1);
    return client.invalidateQueries();
  };
}
export function useCreate(kind: "space" | "kb") {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: unknown) => service.create(kind, body),
    onSuccess: () => client.invalidateQueries({ queryKey: kind === "space" ? keys.spaces : ["libraries"] }),
  });
}
export function useSetDefault() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ space, version }: { space: string; version: number }) => service.setDefault(space, version),
    onSuccess: (data) => {
      client.setQueryData(keys.defaults, { ...data, available: true });
    },
  });
}
export function useSettings(kb: KB) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (chunking: KB["value"]["chunking"]) => service.settings(kb, chunking),
    onSuccess: (updated) => {
      client.setQueryData<{ items: KB[] }>(
        keys.libraries(kb.space_scope_id),
        (previous) =>
          previous && {
            ...previous,
            items: previous.items.map((item) => (item.scope_id === updated.scope_id ? updated : item)),
          },
      );
    },
  });
}
export function useImport(scope: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (
      input: { files: { file: File; key: string }[] } | { root: string; paths: string[]; idempotency_key: string },
    ) => {
      if ("root" in input) {
        const receipts = (await service.imports(scope, input)).items;
        for (const receipt of receipts) {
          if (receipt.accepted) trackProcessing(client, scope, receipt);
        }
        return receipts;
      }
      const receipts = [];
      for (const { file, key } of input.files) {
        try {
          const receipt = { ...(await service.upload(scope, file, key)), path: file.webkitRelativePath || file.name };
          // Track each acceptance immediately, even if later files are slow or this view is unmounted.
          if (receipt.accepted) trackProcessing(client, scope, receipt);
          receipts.push(receipt);
        } catch (error) {
          receipts.push({ path: file.name, accepted: false, error: String(error) });
        }
      }
      return receipts;
    },
    onSuccess: () => client.invalidateQueries({ queryKey: keys.documents(scope) }),
  });
}
export function useDocumentAction(target: Target) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (
      command: { kind: "edit"; edit: Edit } | { kind: "forget" } | { kind: "reprocess"; replace: boolean; key: string },
    ) => {
      if (command.kind === "edit") return service.edit(target, command.edit);
      if (command.kind === "forget") return service.forget(target);
      return service.reprocess(target, command.replace, command.key);
    },
    onSuccess: async (data, command) => {
      if (command.kind === "reprocess") trackProcessing(client, target.scope, data);
      else markDocumentChange(client, target.scope);
      await Promise.all(
        [
          keys.documents(target.scope),
          keys.head(target.scope, target.id),
          keys.governance(target.scope, target.id),
          keys.revisions(target.scope, target.id),
        ].map((queryKey) => client.invalidateQueries({ queryKey })),
      );
    },
  });
}
export function useRetry(scope: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ request, key, replace }: { request: string; key: string; replace: boolean }) =>
      service.retry(scope, request, key, replace),
    onSuccess: (data) => {
      trackProcessing(client, scope, data);
      return client.invalidateQueries({ queryKey: keys.documents(scope) });
    },
  });
}
export const useRetrieve = () =>
  useMutation({
    mutationFn: ({ space, scopes, query, mode }: { space: string; scopes: string[]; query: string; mode: string }) =>
      service.retrieve(space, scopes, query, mode),
  });

export type ProcessingTarget = { scope: string; document: string; request: string };
export const useProcessingRequests = () =>
  useQuery({ queryKey: keys.processing, initialData: [] as ProcessingTarget[], enabled: false, gcTime: Infinity });
export function trackProcessing(client: ReturnType<typeof useQueryClient>, scope: string, data: unknown) {
  if (
    !data ||
    typeof data !== "object" ||
    !("request_id" in data) ||
    !("document_id" in data) ||
    typeof data.request_id !== "string" ||
    typeof data.document_id !== "string"
  )
    return;
  const target = { scope, document: data.document_id, request: data.request_id };
  client.setQueryData<ProcessingTarget[]>(keys.processing, (previous) =>
    previous?.some((item) => item.scope === scope && item.request === target.request)
      ? previous
      : [...(previous ?? []), target],
  );
}

export type BatchDocument = { target: Target; title: string; key: string; modified: boolean };
export function useBatchDocuments() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (command: { kind: "forget" | "reprocess"; items: BatchDocument[]; replace: boolean }) => {
      const results: { id: string; title: string; status: string; error?: string }[] = [];
      // Each request retains the confirmed Scope and revision, even if the user navigates away.
      // Sequential submission bounds load and isolates partial failures.
      for (const { target, title, key, modified } of command.items) {
        try {
          if (command.kind === "reprocess" && modified && !command.replace)
            throw new Error("文档包含人工修改，请明确允许覆盖后再重新解析");
          const data = command.kind === "forget"
            ? await service.forget(target)
            : await service.reprocess(target, command.replace, key);
          if (command.kind === "reprocess") trackProcessing(client, target.scope, data);
          results.push({ id: target.id, title, status: command.kind === "forget" ? "已遗忘" : "已提交解析" });
        } catch (error) {
          results.push({ id: target.id, title, status: "失败", error: String(error) });
        } finally {
          // A lost response may still have committed: refresh authoritative state on every outcome.
          if (command.kind === "forget") markDocumentChange(client, target.scope);
          await Promise.all([
            keys.documents(target.scope), keys.head(target.scope, target.id),
            keys.governance(target.scope, target.id), keys.revisions(target.scope, target.id),
          ].map((queryKey) => client.invalidateQueries({ queryKey })));
        }
      }
      return results;
    },
  });
}
