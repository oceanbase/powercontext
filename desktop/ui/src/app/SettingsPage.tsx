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

/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 */

import { ArrowUpRight } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { Switch } from "../components/ui/switch";
import type { DesktopPreferences } from "./desktop-preferences";

const docs = {
  zh: "https://github.com/oceanbase/powercontext/blob/master/desktop/README.zh.md",
  en: "https://github.com/oceanbase/powercontext/blob/master/desktop/README.md",
};

export function SettingsPage({
  preferences,
  onPreferenceChange,
  preview = false,
}: {
  preferences: DesktopPreferences;
  preview?: boolean;
  onPreferenceChange: <Key extends keyof DesktopPreferences>(
    key: Key,
    value: DesktopPreferences[Key],
  ) => void;
}) {
  const { language, theme, notificationsEnabled, openDesktopAtLogin } =
    preferences;
  const zh = language === "zh";
  const unavailable = zh ? "当前版本暂不可用" : "Unavailable in this build";

  return (
    <div className="settings-page">
      <header className="settings-heading">
        <h2>{zh ? "设置" : "Settings"}</h2>
      </header>

      <section className="settings-section" aria-labelledby="general-settings">
        <h3 id="general-settings">{zh ? "通用" : "General"}</h3>
        <div className="settings-list settings-panel">
          <div className="settings-control-row">
            <span>
              <strong>{zh ? "语言" : "Language"}</strong>
              <small>
                {zh ? "选择界面显示语言" : "Choose the interface language"}
              </small>
            </span>
            <Select
              value={language}
              onValueChange={(value) =>
                onPreferenceChange("language", value as "zh" | "en")
              }
            >
              <SelectTrigger
                id="language"
                className="settings-select-trigger"
                aria-label={zh ? "语言" : "Language"}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="zh">简体中文</SelectItem>
                <SelectItem value="en">English</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="settings-control-row">
            <span>
              <strong>{zh ? "外观" : "Appearance"}</strong>
              <small>
                {zh ? "调整 Desktop 的显示主题" : "Change the Desktop theme"}
              </small>
            </span>
            <Select
              value={theme}
              onValueChange={(value) =>
                onPreferenceChange(
                  "theme",
                  value as DesktopPreferences["theme"],
                )
              }
            >
              <SelectTrigger
                id="theme"
                className="settings-select-trigger"
                aria-label={zh ? "外观" : "Theme"}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="system">
                  {zh ? "跟随系统" : "System"}
                </SelectItem>
                <SelectItem value="light">{zh ? "浅色" : "Light"}</SelectItem>
                <SelectItem value="dark">{zh ? "深色" : "Dark"}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </section>

      <section className="settings-section" aria-labelledby="desktop-settings">
        <h3 id="desktop-settings">Desktop</h3>
        <div className="settings-list settings-panel">
          <div className="settings-control-row">
            <span>
              <strong>{zh ? "系统通知" : "System notifications"}</strong>
              <small>
                {!preview
                  ? unavailable
                  : zh
                    ? "服务异常、Agent 接入异常或 Desktop 有新版本时提醒你"
                    : "Get notified about service issues, Agent connection issues, and Desktop updates"}
              </small>
            </span>
            <Switch
              checked={preview && notificationsEnabled}
              disabled={!preview}
              onCheckedChange={(checked) =>
                onPreferenceChange("notificationsEnabled", checked)
              }
              aria-label={zh ? "系统通知" : "System notifications"}
            />
          </div>
          <div className="settings-control-row">
            <span>
              <strong>
                {zh ? "登录时打开 Desktop" : "Open Desktop at login"}
              </strong>
              <small>
                {!preview
                  ? unavailable
                  : zh
                    ? "登录电脑后自动打开 Desktop，不会启动 PowerContext 服务"
                    : "Open Desktop when you sign in without starting the PowerContext service"}
              </small>
            </span>
            <Switch
              checked={preview && openDesktopAtLogin}
              disabled={!preview}
              onCheckedChange={(checked) =>
                onPreferenceChange("openDesktopAtLogin", checked)
              }
              aria-label={zh ? "登录时打开 Desktop" : "Open Desktop at login"}
            />
          </div>
        </div>
      </section>

      <section className="settings-section" aria-labelledby="support-settings">
        <h3 id="support-settings">{zh ? "帮助与关于" : "Help and about"}</h3>
        <div className="settings-link-list settings-panel">
          <a href={docs[language]} target="_blank" rel="noreferrer">
            <span>
              <strong>{zh ? "使用帮助" : "Usage help"}</strong>
              <small>
                {zh
                  ? "查看连接服务、选择空间和使用记忆的说明"
                  : "Learn how to connect a service, choose a space, and use memories"}
              </small>
            </span>
            <ArrowUpRight aria-hidden="true" />
          </a>
          <a
            href="https://github.com/oceanbase/powercontext/issues"
            target="_blank"
            rel="noreferrer"
          >
            <span>
              <strong>{zh ? "问题反馈" : "Report an issue"}</strong>
              <small>
                {zh
                  ? "向 PowerContext 项目反馈问题"
                  : "Send feedback to the PowerContext project"}
              </small>
            </span>
            <ArrowUpRight aria-hidden="true" />
          </a>
        </div>
      </section>
    </div>
  );
}
