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
import React from "react";
import { createRoot } from "react-dom/client";
import { ConfigProvider } from "@oceanbase/design";
import zhCN from "@oceanbase/design/es/locale/zh-CN";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { markDocumentChange } from "./hooks";
import { isForbidden } from "./services";
import "@oceanbase/design/dist/design.min.css";
import "./styles.scss";
const client: QueryClient = new QueryClient({
  queryCache: new QueryCache({
    onError(error, query) {
      const scope = query.meta?.documentScope;
      if (typeof scope === "string" && isForbidden(error)) markDocumentChange(client, scope);
    },
  }),
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, gcTime: 0 }, mutations: { retry: false } },
});
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider locale={zhCN} card={{ divided: false }} button={{ autoInsertSpace: false }}>
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>
    </ConfigProvider>
  </React.StrictMode>,
);
