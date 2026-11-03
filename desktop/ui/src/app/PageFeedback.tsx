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

import type { ReactNode } from "react";
import { connectionError, connectionErrorCode } from "./connection-messages";
import { messages, type Language } from "./messages";

export function PageFeedback({
  kind,
  title,
  children,
  action,
  compact = false,
  headingLevel,
}: {
  kind: "loading" | "empty" | "unavailable" | "error" | "success" | "warning";
  title: string;
  children?: ReactNode;
  action?: ReactNode;
  compact?: boolean;
  headingLevel?: 3;
}) {
  const Container = compact ? "div" : "section";
  const Title = headingLevel === 3 ? "h3" : compact ? "p" : "h2";
  return (
    <Container
      className={`${compact ? "feedback-inline" : "card stack"} page-feedback ${kind}`}
      role={
        kind === "error" || kind === "warning"
          ? "alert"
          : kind === "loading" || kind === "success"
            ? "status"
            : undefined
      }
    >
      <Title className="feedback-title">{title}</Title>
      {children && <div className="feedback-body">{children}</div>}
      {action && <div className="actions">{action}</div>}
    </Container>
  );
}

export function ErrorFeedback({
  error,
  language,
  title,
  action,
  children,
}: {
  error: unknown;
  language: Language;
  title?: string;
  action?: ReactNode;
  children?: ReactNode;
}) {
  const t = messages[language];
  const code = connectionErrorCode(error);
  const hint =
    code === "forbidden"
      ? t.recoverAccess
      : code === "unauthorized" ||
          code === "credential_missing" ||
          code === "invalid_credential" ||
          code === "authentication_unavailable" ||
          code === "credential_unavailable"
        ? t.recoverCredentials
        : code === "compatibility_unverified" || code === "invalid_response"
          ? t.recoverCompatibility
          : code === "scope_required"
            ? t.recoverScope
            : code === "stale_context" || code === "not_connected"
              ? t.recoverContext
              : code === "cursor_expired"
                ? t.recoverCursor
                : t.recoverOperation;
  return (
    <PageFeedback
      compact
      kind="error"
      title={title ?? connectionError(error, language)}
      action={action}
    >
      {title && <p>{connectionError(error, language)}</p>}
      {children}
      <p>{hint}</p>
    </PageFeedback>
  );
}
