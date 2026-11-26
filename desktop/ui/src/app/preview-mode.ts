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

export function resolvePreviewMode(
  development: boolean,
  mode: string,
  search: string,
): "connected" | "disconnected" | "remote-only" | "local-only" | null {
  if (!development || mode === "test") return null;
  const requested = new URLSearchParams(search).get("preview");
  if (
    requested === "connected" ||
    requested === "disconnected" ||
    requested === "remote-only" ||
    requested === "local-only"
  )
    return requested;
  if (requested === "real") return null;
  return mode === "preview" ? "connected" : null;
}
