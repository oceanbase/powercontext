import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Toggle } from '../../shared/forms/Toggle';
import { Slider } from '../../shared/forms/Slider';
import { Button } from '../../shared/forms/Button';
import './CaptureSettings.css';
/**
 * 捕获设置组件
 * 配置文本捕获行为
 */
export function CaptureSettings({ onSave }) {
    const { settings, updateSettings } = useSettingsStore();
    const [autoSave, setAutoSave] = useState(false);
    const [minSelectionLength, setMinSelectionLength] = useState(10);
    const [includePageContext, setIncludePageContext] = useState(true);
    const [isSaving, setIsSaving] = useState(false);
    // 从 store 加载初始值
    useEffect(() => {
        if (settings) {
            setAutoSave(settings.capture.autoSave);
            setMinSelectionLength(settings.capture.minSelectionLength);
            setIncludePageContext(settings.capture.includePageContext);
        }
    }, [settings]);
    /**
     * 保存设置
     */
    const handleSave = async () => {
        setIsSaving(true);
        try {
            await updateSettings({
                capture: {
                    autoSave,
                    minSelectionLength,
                    includePageContext
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
    /**
     * 重置为默认值
     */
    const handleReset = () => {
        setAutoSave(false);
        setMinSelectionLength(10);
        setIncludePageContext(true);
    };
    return (_jsxs("div", { className: "capture-settings", children: [_jsxs("div", { className: "settings-header", children: [_jsx("h2", { children: "\u6355\u83B7\u8BBE\u7F6E" }), _jsx("p", { children: "\u914D\u7F6E\u6587\u672C\u9009\u62E9\u548C\u8BB0\u5FC6\u6355\u83B7\u884C\u4E3A" })] }), _jsxs("div", { className: "settings-form", children: [_jsx(Toggle, { label: "\u81EA\u52A8\u4FDD\u5B58", description: "\u9009\u4E2D\u6587\u672C\u540E\u81EA\u52A8\u4FDD\u5B58\u4E3A\u8BB0\u5FC6\uFF0C\u65E0\u9700\u70B9\u51FB\u4FDD\u5B58\u6309\u94AE", checked: autoSave, onChange: setAutoSave }), _jsx(Slider, { label: "\u6700\u5C0F\u9009\u62E9\u957F\u5EA6", value: minSelectionLength, min: 5, max: 100, step: 5, onChange: setMinSelectionLength, formatValue: (v) => `${v} 字符`, helpText: "\u9009\u62E9\u7684\u6587\u672C\u81F3\u5C11\u9700\u8981\u591A\u5C11\u5B57\u7B26\u624D\u4F1A\u89E6\u53D1\u6355\u83B7" }), _jsx(Toggle, { label: "\u5305\u542B\u9875\u9762\u4E0A\u4E0B\u6587", description: "\u81EA\u52A8\u8BB0\u5F55\u9875\u9762\u6807\u9898\u3001URL \u7B49\u4E0A\u4E0B\u6587\u4FE1\u606F", checked: includePageContext, onChange: setIncludePageContext }), _jsxs("div", { className: "form-actions", children: [_jsx(Button, { variant: "primary", onClick: handleSave, loading: isSaving, children: "\u4FDD\u5B58\u8BBE\u7F6E" }), _jsx(Button, { variant: "ghost", onClick: handleReset, disabled: isSaving, children: "\u91CD\u7F6E" })] })] })] }));
}
