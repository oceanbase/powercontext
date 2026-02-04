import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useEffect } from 'react';
import { useSettingsStore } from '@/store/useSettingsStore';
import { ConnectionSettings } from './components/ConnectionSettings';
import { UserSettings } from './components/UserSettings';
import { CaptureSettings } from './components/CaptureSettings';
import { UISettings } from './components/UISettings';
import { AdvancedSettings } from './components/AdvancedSettings';
import './App.css';
/**
 * 导航项配置
 */
const NAV_ITEMS = [
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
    const [currentPage, setCurrentPage] = useState('connection');
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
                return _jsx(ConnectionSettings, { onSave: handleSave });
            case 'user':
                return _jsx(UserSettings, { onSave: handleSave });
            case 'capture':
                return _jsx(CaptureSettings, { onSave: handleSave });
            case 'ui':
                return _jsx(UISettings, { onSave: handleSave });
            case 'advanced':
                return _jsx(AdvancedSettings, { onSave: handleSave });
            default:
                return _jsx(ConnectionSettings, { onSave: handleSave });
        }
    };
    return (_jsxs("div", { className: "options-app", children: [_jsx("header", { className: "app-header", children: _jsxs("div", { className: "header-content", children: [_jsx("h1", { className: "app-title", children: "PowerMem \u8BBE\u7F6E" }), _jsx("p", { className: "app-subtitle", children: "\u914D\u7F6E\u60A8\u7684\u8BB0\u5FC6\u7BA1\u7406\u6269\u5C55" })] }) }), _jsxs("div", { className: "app-body", children: [_jsx("nav", { className: "app-sidebar", children: NAV_ITEMS.map((item) => (_jsxs("button", { className: `nav-item ${currentPage === item.id ? 'active' : ''}`, onClick: () => setCurrentPage(item.id), children: [_jsx("span", { className: "nav-icon", children: item.icon }), _jsx("span", { className: "nav-label", children: item.label })] }, item.id))) }), _jsx("main", { className: "app-content", children: renderCurrentPage() })] }), saveNotification && (_jsx("div", { className: "save-notification", children: "\u2705 \u8BBE\u7F6E\u5DF2\u4FDD\u5B58" }))] }));
}
export default App;
