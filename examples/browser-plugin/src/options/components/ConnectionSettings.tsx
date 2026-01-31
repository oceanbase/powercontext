import React, { useState, useEffect } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Input } from '../../shared/forms/Input';
import { Button } from '../../shared/forms/Button';
import { ConnectionStatus } from './ConnectionStatus';
import { ConnectionTester, ConnectionTestResult } from '../../services/connection/connectionTester';
import { validateUrl, validateApiKey, validateTimeout } from '../../utils/validation';
import './ConnectionSettings.css';

/**
 * ConnectionSettings 组件属性
 */
interface ConnectionSettingsProps {
  /** 保存回调 */
  onSave?: () => void;
}

/**
 * 连接设置组件
 * 配置 PowerMem 服务器连接参数
 */
export function ConnectionSettings({ onSave }: ConnectionSettingsProps): JSX.Element {
  const { settings, updateSettings } = useSettingsStore();
  
  // 表单状态
  const [serverUrl, setServerUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [timeout, setTimeout] = useState(30000);
  
  // UI 状态
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [isSaving, setIsSaving] = useState(false);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
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
  const validateForm = (): boolean => {
    const newErrors: Record<string, string> = {};

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
    } catch (error) {
      console.error('Connection test error:', error);
      setTestResult({
        success: false,
        error: '测试连接时发生错误'
      });
    } finally {
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
    } catch (error) {
      console.error('Save settings error:', error);
      alert('保存设置失败，请重试');
    } finally {
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

  return (
    <div className="connection-settings">
      <div className="settings-header">
        <h2>服务器连接配置</h2>
        <p>配置 PowerMem 服务器地址和认证信息</p>
      </div>

      <div className="settings-form">
        <Input
          type="url"
          label="服务器 URL"
          placeholder="http://localhost:8000"
          value={serverUrl}
          onChange={(value) => {
            setServerUrl(value as string);
            setErrors(prev => ({ ...prev, serverUrl: '' }));
          }}
          error={errors.serverUrl}
          helpText="PowerMem 服务器地址，包含协议和端口"
          required
        />

        <Input
          type="password"
          label="API 密钥"
          placeholder="输入您的 API Key"
          value={apiKey}
          onChange={(value) => {
            setApiKey(value as string);
            setErrors(prev => ({ ...prev, apiKey: '' }));
          }}
          error={errors.apiKey}
          helpText="用于认证的 API 密钥"
          required
        />

        <Input
          type="number"
          label="请求超时时间 (ms)"
          placeholder="30000"
          value={timeout}
          onChange={(value) => {
            setTimeout(value as number);
            setErrors(prev => ({ ...prev, timeout: '' }));
          }}
          error={errors.timeout}
          helpText="请求超时时间，范围 1000-120000 毫秒"
        />

        <div className="form-actions">
          <Button
            variant="secondary"
            onClick={handleTestConnection}
            loading={isTesting}
            disabled={isSaving}
          >
            测试连接
          </Button>

          <Button
            variant="primary"
            onClick={handleSave}
            loading={isSaving}
            disabled={isTesting}
          >
            保存设置
          </Button>

          <Button
            variant="ghost"
            onClick={handleReset}
            disabled={isSaving || isTesting}
          >
            重置
          </Button>
        </div>

        <ConnectionStatus result={testResult} isTesting={isTesting} />
      </div>
    </div>
  );
}
