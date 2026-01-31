import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Toggle } from '../../shared/forms/Toggle';
import { Slider } from '../../shared/forms/Slider';
import { Button } from '../../shared/forms/Button';
import './AdvancedSettings.css';
/**
 * 高级设置组件
 * 配置实验性功能和性能参数
 */
export function AdvancedSettings({ onSave }) {
    const { settings, updateSettings } = useSettingsStore();
    const [enableContextualSuggestions, setEnableContextualSuggestions] = useState(true);
    const [maxRecentMemories, setMaxRecentMemories] = useState(50);
    const [isSaving, setIsSaving] = useState(false);
    // 从 store 加载初始值
    useEffect(() => {
        if (settings) {
            setEnableContextualSuggestions(settings.advanced.enableContextualSuggestions);
            setMaxRecentMemories(settings.advanced.maxRecentMemories);
        }
    }, [settings]);
    /**
     * 保存设置
     */
    const handleSave = async () => {
        setIsSaving(true);
        try {
            await updateSettings({
                advanced: {
                    enableContextualSuggestions,
                    maxRecentMemories
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
        setEnableContextualSuggestions(true);
        setMaxRecentMemories(50);
    };
    return (_jsxs("div", { className: "advanced-settings", children: [_jsxs("div", { className: "settings-header", children: [_jsx("h2", { children: "\u9AD8\u7EA7\u8BBE\u7F6E" }), _jsx("p", { children: "\u914D\u7F6E\u5B9E\u9A8C\u6027\u529F\u80FD\u548C\u6027\u80FD\u53C2\u6570" })] }), _jsxs("div", { className: "settings-form", children: [_jsx("div", { className: "warning-banner", children: "\u26A0\uFE0F \u8FD9\u4E9B\u8BBE\u7F6E\u53EF\u80FD\u5F71\u54CD\u6269\u5C55\u6027\u80FD\uFF0C\u8BF7\u8C28\u614E\u4FEE\u6539" }), _jsx(Toggle, { label: "\u542F\u7528\u4E0A\u4E0B\u6587\u5EFA\u8BAE", description: "\u6839\u636E\u5F53\u524D\u9875\u9762\u5185\u5BB9\u63D0\u4F9B\u667A\u80FD\u8BB0\u5FC6\u5EFA\u8BAE\uFF08\u5B9E\u9A8C\u6027\u529F\u80FD\uFF09", checked: enableContextualSuggestions, onChange: setEnableContextualSuggestions }), _jsx(Slider, { label: "\u6700\u5927\u6700\u8FD1\u8BB0\u5FC6\u6570", value: maxRecentMemories, min: 10, max: 200, step: 10, onChange: setMaxRecentMemories, formatValue: (v) => `${v} 条`, helpText: "Popup \u4E2D\u663E\u793A\u7684\u6700\u8FD1\u8BB0\u5FC6\u6570\u91CF" }), _jsxs("div", { className: "form-actions", children: [_jsx(Button, { variant: "primary", onClick: handleSave, loading: isSaving, children: "\u4FDD\u5B58\u8BBE\u7F6E" }), _jsx(Button, { variant: "ghost", onClick: handleReset, disabled: isSaving, children: "\u91CD\u7F6E" })] })] })] }));
}
