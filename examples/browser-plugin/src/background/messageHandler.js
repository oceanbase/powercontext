import { PowerMemClient } from '@/services/api/client';
import { StorageService } from '@/services/storage/storageService';
import { ErrorHandler } from '@/utils/errorHandler';
/**
 * 消息处理器
 * 处理来自 Content Script 和 Popup 的所有消息
 */
export class MessageHandler {
    /**
     * 构造函数
     */
    constructor() {
        this.apiClient = null;
        console.log('[MessageHandler] Initialized');
    }
    /**
     * 处理消息
     * @param message - 消息对象
     * @param sender - 消息发送者
     * @returns 消息响应
     */
    async handle(message, sender) {
        console.log('[MessageHandler] Received message:', message.type, message);
        try {
            switch (message.type) {
                case 'SAVE_MEMORY':
                    return await this.handleSaveMemory(message.payload);
                case 'SEARCH_MEMORIES':
                    return await this.handleSearchMemories(message.payload);
                case 'GET_MEMORIES':
                    return await this.handleGetMemories(message.payload);
                case 'GET_SETTINGS':
                    return await this.handleGetSettings();
                case 'UPDATE_SETTINGS':
                    return await this.handleUpdateSettings(message.payload);
                case 'TEST_CONNECTION':
                    return await this.handleTestConnection();
                default:
                    return {
                        success: false,
                        error: `Unknown message type: ${message.type}`,
                    };
            }
        }
        catch (error) {
            const errorMessage = ErrorHandler.handle(error);
            ErrorHandler.log(error, { message, sender });
            return {
                success: false,
                error: errorMessage,
            };
        }
    }
    /**
     * 处理保存记忆消息
     * @param payload - 保存参数
     * @returns 保存结果
     */
    async handleSaveMemory(payload) {
        this.validatePayload(payload, ['content', 'pageContext']);
        const settings = await StorageService.getSettings();
        const client = await this.getApiClient();
        // 构建记忆对象
        const memory = {
            content: payload.content,
            user_id: settings.user.userId,
            agent_id: settings.user.defaultAgentId || 'work',
            metadata: {
                source: 'browser_extension',
                type: 'web_selection',
                url: payload.pageContext.url,
                title: payload.pageContext.title,
                domain: payload.pageContext.domain,
                timestamp: new Date().toISOString(),
                ...(payload.pageContext.metaDescription && {
                    description: payload.pageContext.metaDescription,
                }),
                ...(payload.pageContext.keywords && {
                    keywords: payload.pageContext.keywords,
                }),
                ...(payload.selectionContext && {
                    surroundingParagraph: payload.selectionContext.surroundingParagraph,
                }),
            },
        };
        const savedMemory = await client.createMemory(memory);
        return {
            success: true,
            data: savedMemory,
        };
    }
    /**
     * 处理搜索记忆消息
     * @param payload - 搜索参数
     * @returns 搜索结果
     */
    async handleSearchMemories(payload) {
        this.validatePayload(payload, ['query']);
        const settings = await StorageService.getSettings();
        const client = await this.getApiClient();
        const results = await client.searchMemories({
            query: payload.query,
            user_id: settings.user.userId,
            agent_id: payload.agent_id || settings.user.defaultAgentId,
            limit: payload.limit || 10,
            filters: payload.filters,
        });
        return {
            success: true,
            data: results,
        };
    }
    /**
     * 处理获取记忆列表消息
     * @param payload - 查询参数
     * @returns 记忆列表
     */
    async handleGetMemories(payload) {
        const settings = await StorageService.getSettings();
        const client = await this.getApiClient();
        const response = await client.getMemories({
            user_id: settings.user.userId,
            agent_id: payload?.agent_id || settings.user.defaultAgentId,
            limit: payload?.limit || 50,
            offset: payload?.offset || 0,
            sort_by: payload?.sort_by || 'created_at',
            order: payload?.order || 'desc',
        });
        return {
            success: true,
            data: response,
        };
    }
    /**
     * 处理获取设置消息
     * @returns 当前设置
     */
    async handleGetSettings() {
        const settings = await StorageService.getSettings();
        return {
            success: true,
            data: settings,
        };
    }
    /**
     * 处理更新设置消息
     * @param payload - 新设置
     */
    async handleUpdateSettings(payload) {
        await StorageService.updateSettings(payload);
        // 重置 API 客户端以使用新设置
        this.apiClient = null;
        return {
            success: true,
            data: await StorageService.getSettings(),
        };
    }
    /**
     * 处理测试连接消息
     * @returns 连接测试结果
     */
    async handleTestConnection() {
        try {
            const client = await this.getApiClient();
            const isHealthy = await client.healthCheck();
            return {
                success: isHealthy,
                data: { healthy: isHealthy },
                error: isHealthy ? undefined : 'Server is not responding',
            };
        }
        catch (error) {
            const errorMessage = ErrorHandler.handle(error);
            return {
                success: false,
                error: errorMessage,
            };
        }
    }
    /**
     * 获取或创建 API 客户端
     * @returns API 客户端实例
     */
    async getApiClient() {
        if (!this.apiClient) {
            const settings = await StorageService.getSettings();
            if (!settings.server.url || !settings.server.apiKey) {
                throw new Error('请先在设置中配置服务器地址和 API 密钥');
            }
            this.apiClient = new PowerMemClient(settings.server.url, settings.server.apiKey);
        }
        return this.apiClient;
    }
    /**
     * 验证消息参数
     * @param payload - 参数对象
     * @param required - 必需字段列表
     * @throws {Error} 当缺少必需字段时
     */
    validatePayload(payload, required) {
        if (!payload) {
            throw new Error('Missing payload');
        }
        for (const field of required) {
            if (!(field in payload)) {
                throw new Error(`Missing required field: ${field}`);
            }
        }
    }
}
