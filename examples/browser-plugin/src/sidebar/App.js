import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useEffect, useState } from 'react';
import { AgentSwitcher } from './components/AgentSwitcher';
import { RelatedMemories } from './components/RelatedMemories';
import { RecentMemories } from './components/RecentMemories';
import './App.css';
/**
 * Sidebar 主组件
 * 显示当前页面相关记忆和最近记忆
 *
 * @returns React 组件
 */
function App() {
    const [pageContext, setPageContext] = useState({
        url: '',
        title: '',
        domain: '',
    });
    const [isLoading, setIsLoading] = useState(true);
    /**
     * 获取当前页面上下文
     */
    const getCurrentPageContext = async () => {
        try {
            // 获取当前活动标签页
            const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
            if (tab && tab.url && tab.title) {
                const url = new URL(tab.url);
                setPageContext({
                    url: tab.url,
                    title: tab.title,
                    domain: url.hostname,
                });
            }
        }
        catch (error) {
            console.error('[Sidebar] Failed to get page context:', error);
        }
        finally {
            setIsLoading(false);
        }
    };
    // 初始加载
    useEffect(() => {
        getCurrentPageContext();
    }, []);
    // 监听标签页切换
    useEffect(() => {
        const handleTabUpdate = (tabId, changeInfo, tab) => {
            if (changeInfo.url || changeInfo.title) {
                getCurrentPageContext();
            }
        };
        const handleTabActivated = () => {
            getCurrentPageContext();
        };
        chrome.tabs.onUpdated.addListener(handleTabUpdate);
        chrome.tabs.onActivated.addListener(handleTabActivated);
        return () => {
            chrome.tabs.onUpdated.removeListener(handleTabUpdate);
            chrome.tabs.onActivated.removeListener(handleTabActivated);
        };
    }, []);
    /**
     * 处理Agent切换
     */
    const handleAgentSwitch = () => {
        // Agent切换后可以重新加载记忆
        // 这里由组件自己管理
    };
    if (isLoading) {
        return (_jsx("div", { className: "sidebar-app", children: _jsx("div", { className: "sidebar-loading", children: "Loading..." }) }));
    }
    return (_jsxs("div", { className: "sidebar-app", children: [_jsxs("div", { className: "sidebar-header", children: [_jsxs("div", { className: "sidebar-logo", children: [_jsx("span", { className: "logo-icon", children: "\uD83E\uDDE0" }), _jsx("span", { className: "logo-text", children: "PowerMem" })] }), _jsx(AgentSwitcher, { onSwitch: handleAgentSwitch })] }), pageContext.url && (_jsxs("div", { className: "page-context", children: [_jsx("div", { className: "page-title", children: pageContext.title }), _jsx("div", { className: "page-url", children: pageContext.domain })] })), pageContext.url && pageContext.title && (_jsx(RelatedMemories, { pageUrl: pageContext.url, pageTitle: pageContext.title })), _jsx(RecentMemories, { limit: 10 })] }));
}
export default App;
