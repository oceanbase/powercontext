import React, { useState, useEffect } from 'react';
import { useSettingsStore } from '@/store/useSettingsStore';
import { Input } from '@/shared/forms/Input';
import { Select, SelectOption } from '@/shared/forms/Select';
import { Button } from '@/shared/forms/Button';
import './UserSettings.css';

/**
 * 预定义智能体列表
 */
const PREDEFINED_AGENTS: SelectOption[] = [
  { value: 'default-agent', label: 'Default', description: '默认智能体' },
  { value: 'work', label: 'Work', description: '工作相关记忆' },
  { value: 'personal', label: 'Personal', description: '个人记忆' },
  { value: 'research', label: 'Research', description: '研究学习' },
];

/**
 * UserSettings 组件属性
 */
interface UserSettingsProps {
  /** 保存回调 */
  onSave?: () => void;
}

/**
 * 用户设置组件
 * 管理用户 ID 和默认智能体配置
 */
export function UserSettings({ onSave }: UserSettingsProps): JSX.Element {
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
  const generateUserId = (): string => {
    return `user-${Date.now()}-${Math.random().toString(36).substring(7)}`;
  };

  /**
   * 重新生成用户 ID
   */
  const handleRegenerateUserId = () => {
    const confirmed = window.confirm(
      '重新生成用户 ID 将创建一个新的用户身份，之前的记忆将无法直接访问。\n\n确定要继续吗？'
    );

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
    } catch (error) {
      console.error('Save settings error:', error);
      alert('保存设置失败，请重试');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="user-settings">
      <div className="settings-header">
        <h2>用户设置</h2>
        <p>管理用户身份和默认智能体</p>
      </div>

      <div className="settings-form">
        <div className="user-id-section">
          <Input
            type="text"
            label="用户 ID"
            value={userId}
            onChange={(value) => setUserId(value as string)}
            helpText="唯一标识您的用户身份"
            disabled
          />
          <Button
            variant="secondary"
            size="small"
            onClick={handleRegenerateUserId}
            disabled={isSaving}
          >
            重新生成
          </Button>
        </div>

        <Select
          label="默认智能体"
          value={defaultAgentId}
          options={PREDEFINED_AGENTS}
          onChange={setDefaultAgentId}
        />

        <div className="agent-description">
          {PREDEFINED_AGENTS.find(a => a.value === defaultAgentId)?.description}
        </div>

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
