import React, { useState, useEffect } from 'react';
import { useSettingsStore } from '../../store/useSettingsStore';
import { Select, SelectOption } from '../../shared/forms/Select';
import { Button } from '../../shared/forms/Button';
import './UISettings.css';

/**
 * 主题选项
 */
const THEME_OPTIONS: SelectOption[] = [
  { value: 'light', label: 'Light', icon: '☀️', description: '浅色主题' },
  { value: 'dark', label: 'Dark', icon: '🌙', description: '深色主题' },
  { value: 'auto', label: 'Auto', icon: '🔄', description: '跟随系统' },
];

/**
 * 语言选项
 */
const LANGUAGE_OPTIONS: SelectOption[] = [
  { value: 'en', label: 'English', icon: '🇺🇸' },
  { value: 'zh', label: '中文', icon: '🇨🇳' },
];

/**
 * UISettings 组件属性
 */
interface UISettingsProps {
  /** 保存回调 */
  onSave?: () => void;
}

/**
 * UI 设置组件
 * 配置主题和语言偏好
 */
export function UISettings({ onSave }: UISettingsProps): JSX.Element {
  const { settings, updateSettings } = useSettingsStore();
  
  const [theme, setTheme] = useState<'light' | 'dark' | 'auto'>('auto');
  const [language, setLanguage] = useState<'en' | 'zh'>('zh');
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
    } catch (error) {
      console.error('Save settings error:', error);
      alert('保存设置失败，请重试');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="ui-settings">
      <div className="settings-header">
        <h2>界面设置</h2>
        <p>自定义扩展的外观和语言</p>
      </div>

      <div className="settings-form">
        <Select
          label="主题"
          value={theme}
          options={THEME_OPTIONS}
          onChange={(value) => setTheme(value as 'light' | 'dark' | 'auto')}
        />

        <Select
          label="语言"
          value={language}
          options={LANGUAGE_OPTIONS}
          onChange={(value) => setLanguage(value as 'en' | 'zh')}
        />

        <div className="form-actions">
          <Button
            variant="primary"
            onClick={handleSave}
            loading={isSaving}
          >
            保存设置
          </Button>
        </div>
      </div>
    </div>
  );
}
