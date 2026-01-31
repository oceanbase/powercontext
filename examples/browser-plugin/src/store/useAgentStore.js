import { create } from 'zustand';
import { AgentManager } from '@/services/agent/agentManager';
/**
 * 使用 Agent 状态 Hook
 */
export const useAgentStore = create((set, get) => ({
    agents: [],
    currentAgent: null,
    isLoading: false,
    error: null,
    /**
     * 设置 Agent 列表
     */
    setAgents: (agents) => {
        set({ agents });
    },
    /**
     * 设置当前 Agent
     */
    setCurrentAgent: (agent) => {
        set({ currentAgent: agent });
    },
    /**
     * 添加 Agent
     */
    addAgent: (agent) => {
        const { agents } = get();
        set({ agents: [...agents, agent] });
    },
    /**
     * 更新 Agent
     */
    updateAgent: (agentId, updates) => {
        const { agents, currentAgent } = get();
        const updatedAgents = agents.map(agent => agent.id === agentId ? { ...agent, ...updates } : agent);
        set({ agents: updatedAgents });
        // 如果更新的是当前 Agent，也更新 currentAgent
        if (currentAgent && currentAgent.id === agentId) {
            set({ currentAgent: { ...currentAgent, ...updates } });
        }
    },
    /**
     * 删除 Agent
     */
    removeAgent: (agentId) => {
        const { agents } = get();
        set({ agents: agents.filter(agent => agent.id !== agentId) });
    },
    /**
     * 加载所有 Agent
     */
    loadAgents: async () => {
        try {
            set({ isLoading: true, error: null });
            // 加载所有 Agent
            const agents = await AgentManager.getAllAgents();
            set({ agents });
            // 加载当前 Agent
            const currentAgent = await AgentManager.getCurrentAgent();
            set({ currentAgent, isLoading: false });
        }
        catch (error) {
            console.error('[useAgentStore] Failed to load agents:', error);
            set({
                error: error instanceof Error ? error.message : 'Failed to load agents',
                isLoading: false
            });
        }
    },
    /**
     * 切换 Agent
     */
    switchAgent: async (agentId) => {
        try {
            set({ isLoading: true, error: null });
            // 切换 Agent
            await AgentManager.switchAgent(agentId);
            // 重新加载当前 Agent
            const currentAgent = await AgentManager.getCurrentAgent();
            set({ currentAgent, isLoading: false });
        }
        catch (error) {
            console.error('[useAgentStore] Failed to switch agent:', error);
            set({
                error: error instanceof Error ? error.message : 'Failed to switch agent',
                isLoading: false
            });
            throw error;
        }
    },
    /**
     * 设置错误信息
     */
    setError: (error) => {
        set({ error });
    },
    /**
     * 清除错误信息
     */
    clearError: () => {
        set({ error: null });
    },
}));
