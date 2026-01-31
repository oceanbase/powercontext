import { create } from 'zustand';
import type { Agent } from '@/types';
import { AgentManager } from '@/services/agent/agentManager';

/**
 * Agent 状态接口
 */
interface AgentState {
  /** 所有 Agent 列表 */
  agents: Agent[];
  
  /** 当前活动 Agent */
  currentAgent: Agent | null;
  
  /** 是否正在加载 */
  isLoading: boolean;
  
  /** 错误信息 */
  error: string | null;
  
  /**
   * 设置 Agent 列表
   * @param agents - Agent 数组
   */
  setAgents: (agents: Agent[]) => void;
  
  /**
   * 设置当前 Agent
   * @param agent - Agent 对象
   */
  setCurrentAgent: (agent: Agent) => void;
  
  /**
   * 添加 Agent
   * @param agent - 新 Agent
   */
  addAgent: (agent: Agent) => void;
  
  /**
   * 更新 Agent
   * @param agentId - Agent ID
   * @param updates - 更新字段
   */
  updateAgent: (agentId: string, updates: Partial<Agent>) => void;
  
  /**
   * 删除 Agent
   * @param agentId - Agent ID
   */
  removeAgent: (agentId: string) => void;
  
  /**
   * 加载所有 Agent
   */
  loadAgents: () => Promise<void>;
  
  /**
   * 切换 Agent
   * @param agentId - 目标 Agent ID
   */
  switchAgent: (agentId: string) => Promise<void>;
  
  /**
   * 设置错误信息
   * @param error - 错误信息
   */
  setError: (error: string | null) => void;
  
  /**
   * 清除错误信息
   */
  clearError: () => void;
}

/**
 * 使用 Agent 状态 Hook
 */
export const useAgentStore = create<AgentState>((set, get) => ({
  agents: [],
  currentAgent: null,
  isLoading: false,
  error: null,
  
  /**
   * 设置 Agent 列表
   */
  setAgents: (agents: Agent[]) => {
    set({ agents });
  },
  
  /**
   * 设置当前 Agent
   */
  setCurrentAgent: (agent: Agent) => {
    set({ currentAgent: agent });
  },
  
  /**
   * 添加 Agent
   */
  addAgent: (agent: Agent) => {
    const { agents } = get();
    set({ agents: [...agents, agent] });
  },
  
  /**
   * 更新 Agent
   */
  updateAgent: (agentId: string, updates: Partial<Agent>) => {
    const { agents, currentAgent } = get();
    
    const updatedAgents = agents.map(agent =>
      agent.id === agentId ? { ...agent, ...updates } : agent
    );
    
    set({ agents: updatedAgents });
    
    // 如果更新的是当前 Agent，也更新 currentAgent
    if (currentAgent && currentAgent.id === agentId) {
      set({ currentAgent: { ...currentAgent, ...updates } });
    }
  },
  
  /**
   * 删除 Agent
   */
  removeAgent: (agentId: string) => {
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
    } catch (error) {
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
  switchAgent: async (agentId: string) => {
    try {
      set({ isLoading: true, error: null });
      
      // 切换 Agent
      await AgentManager.switchAgent(agentId);
      
      // 重新加载当前 Agent
      const currentAgent = await AgentManager.getCurrentAgent();
      set({ currentAgent, isLoading: false });
    } catch (error) {
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
  setError: (error: string | null) => {
    set({ error });
  },
  
  /**
   * 清除错误信息
   */
  clearError: () => {
    set({ error: null });
  },
}));
