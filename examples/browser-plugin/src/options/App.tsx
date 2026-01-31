import React, { useState, useEffect } from 'react';
import { useSettingsStore } from '../store/useSettingsStore';
import { ConnectionSettings } from './components/ConnectionSettings';
import { UserSettings } from './components/UserSettings';
import { CaptureSettings } from './components/CaptureSettings';
import { UISettings } from './components/UISettings';
import { AdvancedSettings } from './components/AdvancedSettings';
import './App.css';

/**
 * 设置页面类型
 */
type SettingsPage = 'connection' | 'user' | 'capture' | 'ui' | 'advanced';

/**
 * 导航项配置
 */
const NAV_ITEMS: Array<{ id: SettingsPage; label: string; icon: string }> = [
  { id: 'connection', label: '服务器连接', icon: '🔌' },
  { id: 'user', label: '用户设置', icon: '👤' },
  { id: 'capture', label: '捕获设置', icon: '📝' },
  { id: 'ui', label: '界面设置', icon: '🎨' },
  { id: 'advanced', label: '高级设置', icon: '⚙️' },
];

/**
 * Options 页面主组件
 * 扩展设置配置界面
 */
function App() {
  const { loadSettings } = useSettingsStore();
  const [currentPage, setCurrentPage] = useState<SettingsPage>('connection');
  const [saveNotification, setSaveNotification] = useState(false);

  // 组件加载时初始化设置
  useEffect(() => {
    loadSettings();
  }, [loadSettings]);

  /**
   * 保存回调
   */
  const handleSave = () => {
    setSaveNotification(true);
    setTimeout(() => setSaveNotification(false), 3000);
  };

  /**
   * 渲染当前页面
   */
  const renderCurrentPage = () => {
    switch (currentPage) {
      case 'connection':
        return <ConnectionSettings onSave={handleSave} />;
      case 'user':
        return <UserSettings onSave={handleSave} />;
      case 'capture':
        return <CaptureSettings onSave={handleSave} />;
      case 'ui':
        return <UISettings onSave={handleSave} />;
      case 'advanced':
        return <AdvancedSettings onSave={handleSave} />;
      default:
        return <ConnectionSettings onSave={handleSave} />;
    }
  };

  return (
    <div className="options-app">
      {/* 头部 */}
      <header className="app-header">
        <div className="header-content">
          <h1 className="app-title">PowerMem 设置</h1>
          <p className="app-subtitle">配置您的记忆管理扩展</p>
        </div>
      </header>

      {/* 主体内容 */}
      <div className="app-body">
        {/* 侧边栏导航 */}
        <nav className="app-sidebar">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${currentPage === item.id ? 'active' : ''}`}
              onClick={() => setCurrentPage(item.id)}
            >
              <span className="nav-icon">{item.icon}</span>
              <span className="nav-label">{item.label}</span>
            </button>
          ))}
        </nav>

        {/* 设置内容 */}
        <main className="app-content">
          {renderCurrentPage()}
        </main>
      </div>

      {/* 保存通知 */}
      {saveNotification && (
        <div className="save-notification">
          ✅ 设置已保存
        </div>
      )}
    </div>
  );
}

export default App;
