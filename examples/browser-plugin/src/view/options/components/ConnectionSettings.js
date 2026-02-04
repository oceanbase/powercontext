import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { useSettingsStore } from '@/store/useSettingsStore';
import { Input } from '@/shared/forms/Input';
import { Button } from '@/shared/forms/Button';
import { ConnectionStatus } from './ConnectionStatus';
import { ConnectionTester } from '@/services/connection/connectionTester';
import { validateUrl, validateApiKey, validateTimeout } from '@/utils/validation';
import './ConnectionSettings.css';
/**
 * 连接设置组件
 * 配置 PowerMem 服务器连接参数
 */
export function ConnectionSettings({ onSave }) {
    const { settings, updateSettings } = useSettingsStore();
    // 表单状态
    const [serverUrl, setServerUrl] = useState('');
    const [apiKey, setApiKey] = useState('');
    const [timeout, setTimeout] = useState(30000);
    // UI 状态
    const [errors, setErrors] = useState({});
    const [isSaving, setIsSaving] = useState(false);
    const [testResult, setTestResult] = useState(null);
    const [isTesting, setIsTesting] = useState(false);
    // 从 store 加载初始值
    useEffect(() => {
        if (settings) {
            setServerUrl(settings.server.url);
            setApiKey(settings.server.apiKey);
            setTimeout(settings.server.timeout);
        }
    }, [settings]);
    /**
     * 验证表单
     */
    const validateForm = () => {
        const newErrors = {};
        // 验证 URL
        const urlResult = validateUrl(serverUrl);
        if (!urlResult.isValid && urlResult.error) {
            newErrors.serverUrl = urlResult.error;
        }
        // 验证 API Key
        const apiKeyResult = validateApiKey(apiKey);
        if (!apiKeyResult.isValid && apiKeyResult.error) {
            newErrors.apiKey = apiKeyResult.error;
        }
        // 验证超时时间
        const timeoutResult = validateTimeout(timeout);
        if (!timeoutResult.isValid && timeoutResult.error) {
            newErrors.timeout = timeoutResult.error;
        }
        setErrors(newErrors);
        return Object.keys(newErrors).length === 0;
    };
    /**
     * 测试连接
     */
    const handleTestConnection = async () => {
        // 先验证表单
        if (!validateForm()) {
            return;
        }
        setIsTesting(true);
        setTestResult(null);
        try {
            const result = await ConnectionTester.test(serverUrl, apiKey);
            setTestResult(result);
        }
        catch (error) {
            console.error('Connection test error:', error);
            setTestResult({
                success: false,
                error: '测试连接时发生错误'
            });
        }
        finally {
            setIsTesting(false);
        }
    };
    /**
     * 保存设置
     */
    const handleSave = async () => {
        // 验证表单
        if (!validateForm()) {
            return;
        }
        setIsSaving(true);
        try {
            await updateSettings({
                server: {
                    url: serverUrl,
                    apiKey: apiKey,
                    timeout: timeout
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
        setServerUrl('http://localhost:8000');
        setApiKey('');
        setTimeout(30000);
        setErrors({});
        setTestResult(null);
    };
    return (_jsxs("div", { className: "connection-settings", children: [_jsxs("div", { className: "settings-header", children: [_jsx("h2", { children: "\u670D\u52A1\u5668\u8FDE\u63A5\u914D\u7F6E" }), _jsx("p", { children: "\u914D\u7F6E PowerMem \u670D\u52A1\u5668\u5730\u5740\u548C\u8BA4\u8BC1\u4FE1\u606F" })] }), _jsxs("div", { className: "settings-form", children: [_jsx(Input, { type: "url", label: "\u670D\u52A1\u5668 URL", placeholder: "http://localhost:8000", value: serverUrl, onChange: (value) => {
                            setServerUrl(value);
                            setErrors(prev => ({ ...prev, serverUrl: '' }));
                        }, error: errors.serverUrl, helpText: "PowerMem \u670D\u52A1\u5668\u5730\u5740\uFF0C\u5305\u542B\u534F\u8BAE\u548C\u7AEF\u53E3", required: true }), _jsx(Input, { type: "password", label: "API \u5BC6\u94A5", placeholder: "\u8F93\u5165\u60A8\u7684 API Key", value: apiKey, onChange: (value) => {
                            setApiKey(value);
                            setErrors(prev => ({ ...prev, apiKey: '' }));
                        }, error: errors.apiKey, helpText: "\u7528\u4E8E\u8BA4\u8BC1\u7684 API \u5BC6\u94A5", required: true }), _jsx(Input, { type: "number", label: "\u8BF7\u6C42\u8D85\u65F6\u65F6\u95F4 (ms)", placeholder: "30000", value: timeout, onChange: (value) => {
                            setTimeout(value);
                            setErrors(prev => ({ ...prev, timeout: '' }));
                        }, error: errors.timeout, helpText: "\u8BF7\u6C42\u8D85\u65F6\u65F6\u95F4\uFF0C\u8303\u56F4 1000-120000 \u6BEB\u79D2" }), _jsxs("div", { className: "form-actions", children: [_jsx(Button, { variant: "secondary", onClick: handleTestConnection, loading: isTesting, disabled: isSaving, children: "\u6D4B\u8BD5\u8FDE\u63A5" }), _jsx(Button, { variant: "primary", onClick: handleSave, loading: isSaving, disabled: isTesting, children: "\u4FDD\u5B58\u8BBE\u7F6E" }), _jsx(Button, { variant: "ghost", onClick: handleReset, disabled: isSaving || isTesting, children: "\u91CD\u7F6E" })] }), _jsx(ConnectionStatus, { result: testResult, isTesting: isTesting })] })] }));
}
