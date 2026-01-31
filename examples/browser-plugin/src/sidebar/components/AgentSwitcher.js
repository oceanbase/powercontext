import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { useState, useRef, useEffect } from 'react';
import { useAgentStore } from '@/store/useAgentStore';
import './AgentSwitcher.css';
/**
 * Agent 切换器组件
 * 显示当前 Agent 和切换菜单
 *
 * @param props - 组件属性
 * @returns React 组件
 */
export function AgentSwitcher(props) {
    const { onSwitch } = props;
    // 状态
    const { agents, currentAgent, switchAgent, loadAgents } = useAgentStore();
    const [isMenuOpen, setIsMenuOpen] = useState(false);
    const menuRef = useRef(null);
    // 加载 Agents
    useEffect(() => {
        loadAgents();
    }, [loadAgents]);
    // 点击外部关闭菜单
    useEffect(() => {
        const handleClickOutside = (event) => {
            if (menuRef.current && !menuRef.current.contains(event.target)) {
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
    const handleAgentSwitch = async (agentId) => {
        try {
            await switchAgent(agentId);
            setIsMenuOpen(false);
            if (onSwitch) {
                onSwitch(agentId);
            }
        }
        catch (error) {
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
        return (_jsx("div", { className: "agent-switcher", children: _jsx("div", { className: "agent-switcher-loading", children: "Loading..." }) }));
    }
    return (_jsxs("div", { className: "agent-switcher", ref: menuRef, children: [_jsxs("button", { type: "button", className: "agent-switcher-current", onClick: toggleMenu, children: [_jsx("span", { className: "agent-icon", children: currentAgent.icon || '📁' }), _jsxs("div", { className: "agent-info", children: [_jsx("div", { className: "agent-name", children: currentAgent.name }), _jsxs("div", { className: "agent-count", children: [currentAgent.memoryCount || 0, " memories"] })] }), _jsx("span", { className: `agent-arrow ${isMenuOpen ? 'open' : ''}`, children: "\u25BC" })] }), isMenuOpen && (_jsxs("div", { className: "agent-switcher-menu", children: [_jsx("div", { className: "agent-menu-header", children: "Switch Context" }), _jsx("div", { className: "agent-menu-list", children: agents.map(agent => (_jsxs("button", { type: "button", className: `agent-menu-item ${agent.id === currentAgent.id ? 'active' : ''}`, onClick: () => handleAgentSwitch(agent.id), children: [_jsx("span", { className: "agent-icon", children: agent.icon || '📁' }), _jsxs("div", { className: "agent-info", children: [_jsx("div", { className: "agent-name", children: agent.name }), agent.description && (_jsx("div", { className: "agent-description", children: agent.description }))] }), agent.id === currentAgent.id && (_jsx("span", { className: "agent-check", children: "\u2713" }))] }, agent.id))) })] }))] }));
}
