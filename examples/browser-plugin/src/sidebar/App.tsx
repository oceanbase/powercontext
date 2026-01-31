import React, { useEffect, useState } from 'react';
import { AgentSwitcher } from './components/AgentSwitcher';
import { RelatedMemories } from './components/RelatedMemories';
import { RecentMemories } from './components/RecentMemories';
import './App.css';

/**
 * 页面上下文
 */
interface PageContext {
  url: string;
  title: string;
  domain: string;
}

/**
 * Sidebar 主组件
 * 显示当前页面相关记忆和最近记忆
 * 
 * @returns React 组件
 */
function App() {
  const [pageContext, setPageContext] = useState<PageContext>({
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
    } catch (error) {
      console.error('[Sidebar] Failed to get page context:', error);
    } finally {
      setIsLoading(false);
    }
  };

  // 初始加载
  useEffect(() => {
    getCurrentPageContext();
  }, []);

  // 监听标签页切换
  useEffect(() => {
    const handleTabUpdate = (tabId: number, changeInfo: chrome.tabs.TabChangeInfo, tab: chrome.tabs.Tab) => {
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
    return (
      <div className="sidebar-app">
        <div className="sidebar-loading">
          Loading...
        </div>
      </div>
    );
  }

  return (
    <div className="sidebar-app">
      {/* Header with Agent Switcher */}
      <div className="sidebar-header">
        <div className="sidebar-logo">
          <span className="logo-icon">🧠</span>
          <span className="logo-text">PowerMem</span>
        </div>
        <AgentSwitcher onSwitch={handleAgentSwitch} />
      </div>

      {/* Page Context Display */}
      {pageContext.url && (
        <div className="page-context">
          <div className="page-title">{pageContext.title}</div>
          <div className="page-url">{pageContext.domain}</div>
        </div>
      )}

      {/* Related Memories */}
      {pageContext.url && pageContext.title && (
        <RelatedMemories
          pageUrl={pageContext.url}
          pageTitle={pageContext.title}
        />
      )}

      {/* Recent Memories */}
      <RecentMemories limit={10} />
    </div>
  );
}

export default App;
