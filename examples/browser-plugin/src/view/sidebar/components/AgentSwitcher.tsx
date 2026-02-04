import React, { useState, useRef, useEffect } from 'react';
import { useAgentStore } from '@/store/useAgentStore';
import type { Agent } from '@/types';
import './AgentSwitcher.css';

/**
 * AgentSwitcher 组件属性
 */
interface AgentSwitcherProps {
  /** 切换回调 */
  onSwitch?: (agentId: string) => void;
}

/**
 * Agent 切换器组件
 * 显示当前 Agent 和切换菜单
 * 
 * @param props - 组件属性
 * @returns React 组件
 */
export function AgentSwitcher(props: AgentSwitcherProps): JSX.Element {
  const { onSwitch } = props;
  
  // 状态
  const { agents, currentAgent, switchAgent, loadAgents } = useAgentStore();
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  
  // 加载 Agents
  useEffect(() => {
    loadAgents();
  }, [loadAgents]);
  
  // 点击外部关闭菜单
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsMenuOpen(false);
      }
    };
    
    if (isMenuOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => {
        document.removeEventListener('mousedown', handleClickOutside);
      };
    }
  }, [isMenuOpen]);
  
  /**
   * 处理 Agent 切换
   */
  const handleAgentSwitch = async (agentId: string) => {
    try {
      await switchAgent(agentId);
      setIsMenuOpen(false);
      
      if (onSwitch) {
        onSwitch(agentId);
      }
    } catch (error) {
      console.error('[AgentSwitcher] Failed to switch agent:', error);
    }
  };
  
  /**
   * 切换菜单
   */
  const toggleMenu = () => {
    setIsMenuOpen(!isMenuOpen);
  };
  
  if (!currentAgent) {
    return (
      <div className="agent-switcher">
        <div className="agent-switcher-loading">Loading...</div>
      </div>
    );
  }
  
  return (
    <div className="agent-switcher" ref={menuRef}>
      {/* 当前 Agent 显示 */}
      <button
        type="button"
        className="agent-switcher-current"
        onClick={toggleMenu}
      >
        <span className="agent-icon">{currentAgent.icon || '📁'}</span>
        <div className="agent-info">
          <div className="agent-name">{currentAgent.name}</div>
          <div className="agent-count">
            {currentAgent.memoryCount || 0} memories
          </div>
        </div>
        <span className={`agent-arrow ${isMenuOpen ? 'open' : ''}`}>▼</span>
      </button>
      
      {/* Agent 列表菜单 */}
      {isMenuOpen && (
        <div className="agent-switcher-menu">
          <div className="agent-menu-header">Switch Context</div>
          <div className="agent-menu-list">
            {agents.map(agent => (
              <button
                key={agent.id}
                type="button"
                className={`agent-menu-item ${
                  agent.id === currentAgent.id ? 'active' : ''
                }`}
                onClick={() => handleAgentSwitch(agent.id)}
              >
                <span className="agent-icon">{agent.icon || '📁'}</span>
                <div className="agent-info">
                  <div className="agent-name">{agent.name}</div>
                  {agent.description && (
                    <div className="agent-description">{agent.description}</div>
                  )}
                </div>
                {agent.id === currentAgent.id && (
                  <span className="agent-check">✓</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
