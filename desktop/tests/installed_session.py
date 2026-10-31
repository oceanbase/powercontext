# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Exercise native session projection and actual clipboard copying in the installed application."""

from __future__ import annotations

import json

import httpx
from installed_workflow import ELEMENT, NOTE, InstalledPage
from real_server import HarnessFailure

RESET = (
    "仅清除此 Desktop 的会话观察，不会删除记忆或取消正在执行的操作。"  # noqa: RUF001 - exact localized UI
    "重置前开始的观察不会重新进入新窗口；采集丢失总数继续保留。"  # noqa: RUF001 - exact localized UI
)


def exercise_session(client: httpx.Client, prefix: str) -> dict[str, object]:
    page = InstalledPage(client, prefix)
    page.button("观察")
    page.click("//button[@role='tab'][normalize-space(.)='本次会话']")
    page.wait(
        "const button = [...document.querySelectorAll('button')].find(b => b.textContent === '预览安全诊断摘要');"
        "return !!button && !button.disabled;"
    )
    page.element("//table/caption[normalize-space(.)='HTTP 请求']")
    page.element("//table/caption[normalize-space(.)='用户操作（原生边界）']")  # noqa: RUF001 - exact localized UI
    page.button("预览安全诊断摘要")
    field = page.field("将要复制的安全诊断文本", "textarea")
    text = page.observe("return arguments[0].value;", [{ELEMENT: field}])
    snapshot = json.loads(text)
    if snapshot["schemaVersion"] != 1 or not snapshot["coverage"]["desktopSessionOnly"]:
        raise HarnessFailure("installed_session_scope_missing")
    if snapshot["coverage"]["persistenceEvidence"] or snapshot["coverage"]["installerOps"]:
        raise HarnessFailure("installed_session_coverage_misrepresented")
    if {record["unit"] for record in snapshot["recent"]} != {"http_request", "user_operation"}:
        raise HarnessFailure("installed_session_measurement_units_missing")
    if NOTE in text or "desktoplostuici" in text or "http://127.0.0.1" in text:
        raise HarnessFailure("installed_session_sensitive_text_exported")
    page.button("复制预览文本")
    page.wait_text("安全诊断摘要已复制。")
    page.button("关闭预览")
    page.button("记忆")
    if page.paste() != text:
        raise HarnessFailure("installed_session_clipboard_differs_from_preview")
    page.clear_note()
    page.button("观察")
    page.button("重置会话观察")
    page.confirm(RESET, accept=False)
    page.element("//table/caption[normalize-space(.)='HTTP 请求']")
    page.button("重置会话观察")
    page.confirm(RESET, accept=True)
    page.wait_text("会话观察已重置。记忆和正在执行的操作不受影响。")
    page.wait_text("尚无已完成的观察")
    page.button("预览安全诊断摘要")
    reset_field = page.field("将要复制的安全诊断文本", "textarea")
    reset = json.loads(page.observe("return arguments[0].value;", [{ELEMENT: reset_field}]))
    if reset["recent"] or reset["operations"] or reset["sessionStartedAtMs"] != snapshot["sessionStartedAtMs"]:
        raise HarnessFailure("installed_session_reset_incorrect")
    if reset["droppedSinceStart"] < snapshot["droppedSinceStart"]:
        raise HarnessFailure("installed_session_reset_concealed_losses")
    page.button("关闭预览")
    page.button("记忆")
    return {
        "nativeRequestAndUserOperationTables": True,
        "nativeSafePreviewWithoutContentOrEndpoints": True,
        "exactPreviewCopiedThroughActualClipboardPaste": True,
        "explicitResetCancelAndConfirm": True,
        "resetPreservesSessionStartAndLifetimeLosses": True,
    }
