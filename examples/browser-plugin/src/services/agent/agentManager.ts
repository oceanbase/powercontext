import { StorageService } from '@/services/storage/storageService';
import { Agent, PREDEFINED_AGENTS } from '@/types/agent';

/**
 * Agent 存储键
 */
const STORAGE_KEY = 'agents';

/**
 * Agent 管理器
 * 负责上下文空间的创建、切换、管理
 */
export class AgentManager {
  /**
   * 获取所有 Agent
   * @returns Agent 列表
   */
  static async getAllAgents(): Promise<Agent[]> {
    try {
      const result = await chrome.storage.local.get(STORAGE_KEY);
      const customAgents = result[STORAGE_KEY] || [];
      
      // 合并预定义 Agent 和自定义 Agent
      return [...PREDEFINED_AGENTS, ...customAgents];
    } catch (error) {
      console.error('[AgentManager] Failed to get agents:', error);
      return PREDEFINED_AGENTS;
    }
  }

  /**
   * 获取当前活动 Agent
   * @returns 当前 Agent
   */
  static async getCurrentAgent(): Promise<Agent> {
    try {
      const settings = await StorageService.getSettings();
      const defaultAgentId = settings.user.defaultAgentId;
      
      const allAgents = await this.getAllAgents();
      const currentAgent = allAgents.find(agent => agent.id === defaultAgentId);
      
      if (!currentAgent) {
        // 如果找不到，返回第一个预定义 Agent
        return PREDEFINED_AGENTS[0];
      }
      
      // 获取记忆数量
      const memoryCount = await this.getAgentMemoryCount(currentAgent.id);
      return { ...currentAgent, memoryCount };
    } catch (error) {
      console.error('[AgentManager] Failed to get current agent:', error);
      return PREDEFINED_AGENTS[0];
    }
  }

  /**
   * 切换 Agent
   * @param agentId - 目标 Agent ID
   */
  static async switchAgent(agentId: string): Promise<void> {
    try {
      // 验证 Agent 是否存在
      const allAgents = await this.getAllAgents();
      const agent = allAgents.find(a => a.id === agentId);
      
      if (!agent) {
        throw new Error(`Agent not found: ${agentId}`);
      }
      
      // 更新设置
      const settings = await StorageService.getSettings();
      await StorageService.updateSettings({
        user: {
          ...settings.user,
          defaultAgentId: agentId,
        },
      });
      
      console.log('[AgentManager] Switched to agent:', agentId);
    } catch (error) {
      console.error('[AgentManager] Failed to switch agent:', error);
      throw error;
    }
  }

  /**
   * 创建自定义 Agent
   * @param agent - Agent 信息
   * @returns 创建的 Agent
   */
  static async createAgent(
    agent: Omit<Agent, 'id' | 'isPredefined' | 'createdAt'>
  ): Promise<Agent> {
    try {
      // 验证名称
      if (!this.validateAgentName(agent.name)) {
        throw new Error('Invalid agent name');
      }
      
      // 检查名称是否重复
      const allAgents = await this.getAllAgents();
      if (allAgents.some(a => a.name.toLowerCase() === agent.name.toLowerCase())) {
        throw new Error('Agent name already exists');
      }
      
      // 创建新 Agent
      const newAgent: Agent = {
        ...agent,
        id: this.generateAgentId(),
        isPredefined: false,
        createdAt: new Date().toISOString(),
        memoryCount: 0,
      };
      
      // 获取现有自定义 Agent
      const result = await chrome.storage.local.get(STORAGE_KEY);
      const customAgents = result[STORAGE_KEY] || [];
      
      // 添加新 Agent
      customAgents.push(newAgent);
      await chrome.storage.local.set({ [STORAGE_KEY]: customAgents });
      
      console.log('[AgentManager] Agent created:', newAgent.id);
      return newAgent;
    } catch (error) {
      console.error('[AgentManager] Failed to create agent:', error);
      throw error;
    }
  }

  /**
   * 更新 Agent
   * @param agentId - Agent ID
   * @param updates - 更新字段
   */
  static async updateAgent(agentId: string, updates: Partial<Agent>): Promise<Agent> {
    try {
      const result = await chrome.storage.local.get(STORAGE_KEY);
      const customAgents = result[STORAGE_KEY] || [];
      
      const agentIndex = customAgents.findIndex((a: Agent) => a.id === agentId);
      if (agentIndex === -1) {
        // 检查是否是预定义 Agent
        const predefinedAgent = PREDEFINED_AGENTS.find(a => a.id === agentId);
        if (predefinedAgent) {
          throw new Error('Cannot update predefined agent');
        }
        throw new Error(`Agent not found: ${agentId}`);
      }
      
      // 验证名称（如果更新了名称）
      if (updates.name && !this.validateAgentName(updates.name)) {
        throw new Error('Invalid agent name');
      }
      
      // 更新 Agent
      const updatedAgent = {
        ...customAgents[agentIndex],
        ...updates,
        // 保护不可修改字段
        id: customAgents[agentIndex].id,
        isPredefined: customAgents[agentIndex].isPredefined,
        createdAt: customAgents[agentIndex].createdAt,
      };
      
      customAgents[agentIndex] = updatedAgent;
      await chrome.storage.local.set({ [STORAGE_KEY]: customAgents });
      
      console.log('[AgentManager] Agent updated:', agentId);
      return updatedAgent;
    } catch (error) {
      console.error('[AgentManager] Failed to update agent:', error);
      throw error;
    }
  }

  /**
   * 删除 Agent
   * 仅允许删除自定义 Agent
   * @param agentId - Agent ID
   */
  static async deleteAgent(agentId: string): Promise<void> {
    try {
      // 检查是否是预定义 Agent
      if (PREDEFINED_AGENTS.some(a => a.id === agentId)) {
        throw new Error('Cannot delete predefined agent');
      }
      
      const result = await chrome.storage.local.get(STORAGE_KEY);
      const customAgents = result[STORAGE_KEY] || [];
      
      const filteredAgents = customAgents.filter((a: Agent) => a.id !== agentId);
      
      if (filteredAgents.length === customAgents.length) {
        throw new Error(`Agent not found: ${agentId}`);
      }
      
      await chrome.storage.local.set({ [STORAGE_KEY]: filteredAgents });
      
      // 如果删除的是当前 Agent，切换到默认 Agent
      const settings = await StorageService.getSettings();
      if (settings.user.defaultAgentId === agentId) {
        await this.switchAgent(PREDEFINED_AGENTS[0].id);
      }
      
      console.log('[AgentManager] Agent deleted:', agentId);
    } catch (error) {
      console.error('[AgentManager] Failed to delete agent:', error);
      throw error;
    }
  }

  /**
   * 获取 Agent 的记忆数量
   * @param agentId - Agent ID
   * @returns 记忆数量
   */
  static async getAgentMemoryCount(agentId: string): Promise<number> {
    try {
      // 这里需要通过消息传递调用后端 API
      // 暂时返回 0，在后端集成时实现
      // TODO: Implement memory count API call
      return 0;
    } catch (error) {
      console.error('[AgentManager] Failed to get memory count:', error);
      return 0;
    }
  }

  /**
   * 生成唯一 Agent ID
   * @returns Agent ID
   */
  private static generateAgentId(): string {
    return `agent_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
  }

  /**
   * 验证 Agent 名称
   * @param name - Agent 名称
   * @returns 是否有效
   */
  private static validateAgentName(name: string): boolean {
    if (!name || typeof name !== 'string') {
      return false;
    }
    
    const trimmedName = name.trim();
    
    // 名称不能为空
    if (trimmedName.length === 0) {
      return false;
    }
    
    // 名称长度限制
    if (trimmedName.length > 50) {
      return false;
    }
    
    return true;
  }
}
