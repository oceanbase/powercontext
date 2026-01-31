import React, { useState, useEffect } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Toggle } from '../../shared/forms/Toggle';
import { Slider } from '../../shared/forms/Slider';
import { Button } from '../../shared/forms/Button';
import './AdvancedSettings.css';

/**
 * AdvancedSettings 组件属性
 */
interface AdvancedSettingsProps {
  /** 保存回调 */
  onSave?: () => void;
}

/**
 * 高级设置组件
 * 配置实验性功能和性能参数
 */
export function AdvancedSettings({ onSave }: AdvancedSettingsProps): JSX.Element {
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
    setEnableContextualSuggestions(true);
    setMaxRecentMemories(50);
  };

  return (
    <div className="advanced-settings">
      <div className="settings-header">
        <h2>高级设置</h2>
        <p>配置实验性功能和性能参数</p>
      </div>

      <div className="settings-form">
        <div className="warning-banner">
          ⚠️ 这些设置可能影响扩展性能，请谨慎修改
        </div>

        <Toggle
          label="启用上下文建议"
          description="根据当前页面内容提供智能记忆建议（实验性功能）"
          checked={enableContextualSuggestions}
          onChange={setEnableContextualSuggestions}
        />

        <Slider
          label="最大最近记忆数"
          value={maxRecentMemories}
          min={10}
          max={200}
          step={10}
          onChange={setMaxRecentMemories}
          formatValue={(v) => `${v} 条`}
          helpText="Popup 中显示的最近记忆数量"
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
