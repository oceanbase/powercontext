import React, { useState, useEffect } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Toggle } from '../../shared/forms/Toggle';
import { Slider } from '../../shared/forms/Slider';
import { Button } from '../../shared/forms/Button';
import './CaptureSettings.css';

/**
 * CaptureSettings 组件属性
 */
interface CaptureSettingsProps {
  /** 保存回调 */
  onSave?: () => void;
}

/**
 * 捕获设置组件
 * 配置文本捕获行为
 */
export function CaptureSettings({ onSave }: CaptureSettingsProps): JSX.Element {
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
    setAutoSave(false);
    setMinSelectionLength(10);
    setIncludePageContext(true);
  };

  return (
    <div className="capture-settings">
      <div className="settings-header">
        <h2>捕获设置</h2>
        <p>配置文本选择和记忆捕获行为</p>
      </div>

      <div className="settings-form">
        <Toggle
          label="自动保存"
          description="选中文本后自动保存为记忆，无需点击保存按钮"
          checked={autoSave}
          onChange={setAutoSave}
        />

        <Slider
          label="最小选择长度"
          value={minSelectionLength}
          min={5}
          max={100}
          step={5}
          onChange={setMinSelectionLength}
          formatValue={(v) => `${v} 字符`}
          helpText="选择的文本至少需要多少字符才会触发捕获"
        />

        <Toggle
          label="包含页面上下文"
          description="自动记录页面标题、URL 等上下文信息"
          checked={includePageContext}
          onChange={setIncludePageContext}
        />

        <div className="form-actions">
          <Button
            variant="primary"
            onClick={handleSave}
            loading={isSaving}
          >
            保存设置
          </Button>

          <Button
            variant="ghost"
            onClick={handleReset}
            disabled={isSaving}
          >
            重置
          </Button>
        </div>
      </div>
    </div>
  );
}
