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
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { keys, markDocumentChange, useProcessingRequests, type ProcessingTarget } from "../hooks";
import { Alert } from "@oceanbase/design";
import { isForbidden, service } from "../services";
function Watch({ target, onForbidden }: { target: ProcessingTarget; onForbidden: (show: boolean) => void }) {
  const client = useQueryClient();
  const settled = useRef(false);
  const status = useQuery({
    queryKey: ["processing-status", target.scope, target.request],
    queryFn: () => service.status(target.scope, target.request),
    refetchInterval: (query) =>
      isForbidden(query.state.error) || ["completed", "failed"].includes(query.state.data?.state || "") ? false : 2000,
  });
  useEffect(() => {
    const denied = isForbidden(status.error);
    if (settled.current || (!denied && !["completed", "failed"].includes(status.data?.state || ""))) return;
    settled.current = true;
    if (denied) onForbidden(true);
    if (denied || status.data?.published_revision != null) markDocumentChange(client, target.scope);
    void Promise.all(
      [
        keys.documents(target.scope),
        keys.head(target.scope, target.document),
        keys.governance(target.scope, target.document),
        keys.revisions(target.scope, target.document),
      ].map((queryKey) => client.invalidateQueries({ queryKey })),
    ).then(() => {
      client.setQueryData<ProcessingTarget[]>(keys.processing, (items) =>
        (items || []).filter((item) => item.scope !== target.scope || item.request !== target.request),
      );
    });
  }, [status.data?.state, status.data?.published_revision, status.error, client, target, onForbidden]);
  return null;
}
export function ProcessingWatcher() {
  const [denied, setDenied] = useState(false);
  return (
    <>
      {denied && (
        <Alert type="warning" showIcon closable onClose={() => setDenied(false)}
          message="部分处理请求已无权查看，已停止跟踪。" />
      )}
      {useProcessingRequests().data.map((target) => (
        <Watch key={`${target.scope}:${target.request}`} target={target} onForbidden={setDenied} />
      ))}
    </>
  );
}
