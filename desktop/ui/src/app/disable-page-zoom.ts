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

export function disablePageZoom(target: Document = document): () => void {
  const preventPinchWheel = (event: WheelEvent) => {
    if (event.ctrlKey) {
      event.preventDefault();
    }
  };
  const preventGestureZoom = (event: Event) => event.preventDefault();

  target.addEventListener("wheel", preventPinchWheel, { passive: false });
  target.addEventListener("gesturestart", preventGestureZoom, {
    passive: false,
  });
  target.addEventListener("gesturechange", preventGestureZoom, {
    passive: false,
  });

  return () => {
    target.removeEventListener("wheel", preventPinchWheel);
    target.removeEventListener("gesturestart", preventGestureZoom);
    target.removeEventListener("gesturechange", preventGestureZoom);
  };
}
