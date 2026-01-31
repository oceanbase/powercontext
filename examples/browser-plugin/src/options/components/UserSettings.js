import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Input } from '../../shared/forms/Input';
import { Select } from '../../shared/forms/Select';
import { Button } from '../../shared/forms/Button';
import './UserSettings.css';
/**
 * 预定义智能体列表
 */
const PREDEFINED_AGENTS = [
    { value: 'default-agent', label: 'Default', description: '默认智能体' },
    { value: 'work', label: 'Work', description: '工作相关记忆' },
    { value: 'personal', label: 'Personal', description: '个人记忆' },
    { value: 'research', label: 'Research', description: '研究学习' },
];
/**
 * 用户设置组件
 * 管理用户 ID 和默认智能体配置
 */
export function UserSettings({ onSave }) {
    const { settings, updateSettings } = useSettingsStore();
    const [userId, setUserId] = useState('');
    const [defaultAgentId, setDefaultAgentId] = useState('default-agent');
    const [isSaving, setIsSaving] = useState(false);
    // 从 store 加载初始值
    useEffect(() => {
        if (settings) {
            setUserId(settings.user.userId || generateUserId());
            setDefaultAgentId(settings.user.defaultAgentId);
        }
    }, [settings]);
    /**
     * 生成新的用户 ID
     */
    const generateUserId = () => {
        return `user-${Date.now()}-${Math.random().toString(36).substring(7)}`;
    };
    /**
     * 重新生成用户 ID
     */
    const handleRegenerateUserId = () => {
        const confirmed = window.confirm('重新生成用户 ID 将创建一个新的用户身份，之前的记忆将无法直接访问。\n\n确定要继续吗？');
        if (confirmed) {
            const newUserId = generateUserId();
            setUserId(newUserId);
        }
    };
    /**
     * 保存设置
     */
    const handleSave = async () => {
        setIsSaving(true);
        try {
            await updateSettings({
                user: {
                    userId,
                    defaultAgentId
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
    return (_jsxs("div", { className: "user-settings", children: [_jsxs("div", { className: "settings-header", children: [_jsx("h2", { children: "\u7528\u6237\u8BBE\u7F6E" }), _jsx("p", { children: "\u7BA1\u7406\u7528\u6237\u8EAB\u4EFD\u548C\u9ED8\u8BA4\u667A\u80FD\u4F53" })] }), _jsxs("div", { className: "settings-form", children: [_jsxs("div", { className: "user-id-section", children: [_jsx(Input, { type: "text", label: "\u7528\u6237 ID", value: userId, onChange: (value) => setUserId(value), helpText: "\u552F\u4E00\u6807\u8BC6\u60A8\u7684\u7528\u6237\u8EAB\u4EFD", disabled: true }), _jsx(Button, { variant: "secondary", size: "small", onClick: handleRegenerateUserId, disabled: isSaving, children: "\u91CD\u65B0\u751F\u6210" })] }), _jsx(Select, { label: "\u9ED8\u8BA4\u667A\u80FD\u4F53", value: defaultAgentId, options: PREDEFINED_AGENTS, onChange: setDefaultAgentId }), _jsx("div", { className: "agent-description", children: PREDEFINED_AGENTS.find(a => a.value === defaultAgentId)?.description }), _jsx("div", { className: "form-actions", children: _jsx(Button, { variant: "primary", onClick: handleSave, loading: isSaving, children: "\u4FDD\u5B58\u8BBE\u7F6E" }) })] })] }));
}
