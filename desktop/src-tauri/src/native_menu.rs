/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

use crate::{error::SafeError, ipc::authorize_window};
use tauri::{
    AppHandle, Runtime,
    menu::{Menu, MenuBuilder, MenuItemBuilder, SubmenuBuilder},
};

pub const CHECK_FOR_UPDATES_ID: &str = "check-for-updates";

#[derive(Clone, Copy)]
struct Labels {
    file: &'static str,
    edit: &'static str,
    view: &'static str,
    window: &'static str,
    help: &'static str,
    about: &'static str,
    check_for_updates: &'static str,
    services: &'static str,
    hide: &'static str,
    hide_others: &'static str,
    show_all: &'static str,
    quit: &'static str,
    close: &'static str,
    undo: &'static str,
    redo: &'static str,
    cut: &'static str,
    copy: &'static str,
    paste: &'static str,
    select_all: &'static str,
    fullscreen: &'static str,
    minimize: &'static str,
    zoom: &'static str,
    bring_all_to_front: &'static str,
}

const ZH: Labels = Labels {
    file: "文件",
    edit: "编辑",
    view: "显示",
    window: "窗口",
    help: "帮助",
    about: "关于 PowerContext Desktop",
    check_for_updates: "检查更新…",
    services: "服务",
    hide: "隐藏 PowerContext Desktop",
    hide_others: "隐藏其他",
    show_all: "全部显示",
    quit: "退出 PowerContext Desktop",
    close: "关闭窗口",
    undo: "撤销",
    redo: "重做",
    cut: "剪切",
    copy: "复制",
    paste: "粘贴",
    select_all: "全选",
    fullscreen: "进入全屏幕",
    minimize: "最小化",
    zoom: "缩放",
    bring_all_to_front: "前置全部窗口",
};

const EN: Labels = Labels {
    file: "File",
    edit: "Edit",
    view: "View",
    window: "Window",
    help: "Help",
    about: "About PowerContext Desktop",
    check_for_updates: "Check for Updates…",
    services: "Services",
    hide: "Hide PowerContext Desktop",
    hide_others: "Hide Others",
    show_all: "Show All",
    quit: "Quit PowerContext Desktop",
    close: "Close Window",
    undo: "Undo",
    redo: "Redo",
    cut: "Cut",
    copy: "Copy",
    paste: "Paste",
    select_all: "Select All",
    fullscreen: "Enter Full Screen",
    minimize: "Minimize",
    zoom: "Zoom",
    bring_all_to_front: "Bring All to Front",
};

fn labels(language: &str) -> Result<Labels, SafeError> {
    match language {
        "zh" => Ok(ZH),
        "en" => Ok(EN),
        _ => Err(SafeError::InvalidInput),
    }
}

pub fn build<R: Runtime>(app: &AppHandle<R>, language: &str) -> Result<Menu<R>, SafeError> {
    let t = labels(language)?;
    let check_for_updates = MenuItemBuilder::with_id(CHECK_FOR_UPDATES_ID, t.check_for_updates)
        .build(app)
        .map_err(|_| SafeError::Storage)?;
    let app_menu = SubmenuBuilder::new(app, "PowerContext Desktop")
        .about_with_text(t.about, None)
        .item(&check_for_updates)
        .separator()
        .services_with_text(t.services)
        .separator()
        .hide_with_text(t.hide)
        .hide_others_with_text(t.hide_others)
        .show_all_with_text(t.show_all)
        .separator()
        .quit_with_text(t.quit)
        .build()
        .map_err(|_| SafeError::Storage)?;
    let file = SubmenuBuilder::new(app, t.file)
        .close_window_with_text(t.close)
        .build()
        .map_err(|_| SafeError::Storage)?;
    let edit = SubmenuBuilder::new(app, t.edit)
        .undo_with_text(t.undo)
        .redo_with_text(t.redo)
        .separator()
        .cut_with_text(t.cut)
        .copy_with_text(t.copy)
        .paste_with_text(t.paste)
        .select_all_with_text(t.select_all)
        .build()
        .map_err(|_| SafeError::Storage)?;
    let view = SubmenuBuilder::new(app, t.view)
        .fullscreen_with_text(t.fullscreen)
        .build()
        .map_err(|_| SafeError::Storage)?;
    let window = SubmenuBuilder::new(app, t.window)
        .minimize_with_text(t.minimize)
        .maximize_with_text(t.zoom)
        .separator()
        .bring_all_to_front_with_text(t.bring_all_to_front)
        .build()
        .map_err(|_| SafeError::Storage)?;
    let help = SubmenuBuilder::new(app, t.help)
        .build()
        .map_err(|_| SafeError::Storage)?;

    MenuBuilder::new(app)
        .items(&[&app_menu, &file, &edit, &view, &window, &help])
        .build()
        .map_err(|_| SafeError::Storage)
}

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn powercontext_sparkle_start();
    fn powercontext_sparkle_check_for_updates();
}

#[cfg(target_os = "macos")]
pub fn start_updater() {
    // Tauri setup and menu callbacks run on AppKit's main thread. Sparkle's
    // controller is deliberately created and retained only on that thread.
    unsafe { powercontext_sparkle_start() }
}

#[cfg(not(target_os = "macos"))]
pub fn start_updater() {}

#[cfg(target_os = "macos")]
pub fn check_for_updates<R: Runtime>(_app: &AppHandle<R>) {
    unsafe { powercontext_sparkle_check_for_updates() }
}

#[cfg(not(target_os = "macos"))]
pub fn check_for_updates<R: Runtime>(_app: &AppHandle<R>) {}

#[tauri::command]
pub fn set_menu_language<R: Runtime>(
    window: tauri::WebviewWindow<R>,
    app: AppHandle<R>,
    language: String,
) -> Result<(), SafeError> {
    authorize_window(window.label())?;
    let menu = build(&app, &language)?;
    app.set_menu(menu).map_err(|_| SafeError::Storage)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn supports_complete_chinese_and_english_menu_labels() {
        assert_eq!(labels("zh").unwrap().file, "文件");
        assert_eq!(labels("zh").unwrap().quit, "退出 PowerContext Desktop");
        assert_eq!(labels("zh").unwrap().check_for_updates, "检查更新…");
        assert_eq!(labels("en").unwrap().edit, "Edit");
        assert_eq!(
            labels("en").unwrap().check_for_updates,
            "Check for Updates…"
        );
        assert_eq!(
            labels("en").unwrap().bring_all_to_front,
            "Bring All to Front"
        );
        assert!(matches!(labels("fr"), Err(SafeError::InvalidInput)));
    }
}
