import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Select } from '../../shared/forms/Select';
import { Button } from '../../shared/forms/Button';
import './UISettings.css';
/**
 * 主题选项
 */
const THEME_OPTIONS = [
    { value: 'light', label: 'Light', icon: '☀️', description: '浅色主题' },
    { value: 'dark', label: 'Dark', icon: '🌙', description: '深色主题' },
    { value: 'auto', label: 'Auto', icon: '🔄', description: '跟随系统' },
];
/**
 * 语言选项
 */
const LANGUAGE_OPTIONS = [
    { value: 'en', label: 'English', icon: '🇺🇸' },
    { value: 'zh', label: '中文', icon: '🇨🇳' },
];
/**
 * UI 设置组件
 * 配置主题和语言偏好
 */
export function UISettings({ onSave }) {
    const { settings, updateSettings } = useSettingsStore();
    const [theme, setTheme] = useState('auto');
    const [language, setLanguage] = useState('zh');
    const [isSaving, setIsSaving] = useState(false);
    // 从 store 加载初始值
    useEffect(() => {
        if (settings) {
            setTheme(settings.ui.theme);
            setLanguage(settings.ui.language);
        }
    }, [settings]);
    /**
     * 保存设置
     */
    const handleSave = async () => {
        setIsSaving(true);
        try {
            await updateSettings({
                ui: {
                    theme,
                    language
                }
            });
            onSave?.();
        }
        catch (error) {
            console.error('Save settings error:', error);
            alert('保存设置失败，请重试');
        }
        finally {
            setIsSaving(false);
        }
    };
    return (_jsxs("div", { className: "ui-settings", children: [_jsxs("div", { className: "settings-header", children: [_jsx("h2", { children: "\u754C\u9762\u8BBE\u7F6E" }), _jsx("p", { children: "\u81EA\u5B9A\u4E49\u6269\u5C55\u7684\u5916\u89C2\u548C\u8BED\u8A00" })] }), _jsxs("div", { className: "settings-form", children: [_jsx(Select, { label: "\u4E3B\u9898", value: theme, options: THEME_OPTIONS, onChange: (value) => setTheme(value) }), _jsx(Select, { label: "\u8BED\u8A00", value: language, options: LANGUAGE_OPTIONS, onChange: (value) => setLanguage(value) }), _jsx("div", { className: "form-actions", children: _jsx(Button, { variant: "primary", onClick: handleSave, loading: isSaving, children: "\u4FDD\u5B58\u8BBE\u7F6E" }) })] })] }));
}
