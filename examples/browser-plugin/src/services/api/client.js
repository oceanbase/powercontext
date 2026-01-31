import axios from 'axios';
import { ApiErrorCode } from '@/types';
/**
 * API 错误类
 */
export class ApiError extends Error {
    /**
     * @param message - 错误消息
     * @param status - HTTP 状态码
     * @param code - 错误代码
     * @param details - 详细信息
     */
    constructor(message, status, code, details) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.code = code;
        this.details = details;
    }
}
/**
 * PowerMem HTTP API 客户端
 * 封装所有与 PowerMem 服务器的通信
 */
export class PowerMemClient {
    /**
     * 构造函数
     * @param serverUrl - PowerMem 服务器地址 (如: http://localhost:8000)
     * @param apiKey - API 密钥
     */
    constructor(serverUrl, apiKey) {
        this.serverUrl = serverUrl.replace(/\/$/, ''); // 移除末尾斜杠
        this.apiKey = apiKey;
        this.client = axios.create({
            baseURL: `${this.serverUrl}/api/v1`,
            timeout: 30000,
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${apiKey}`,
            },
        });
        // 请求拦截器
        this.client.interceptors.request.use((config) => {
            console.log('[PowerMem API] Request:', config.method?.toUpperCase(), config.url);
            return config;
        }, (error) => {
            return Promise.reject(error);
        });
        // 响应拦截器
        this.client.interceptors.response.use((response) => {
            console.log('[PowerMem API] Response:', response.status, response.config.url);
            return response;
        }, (error) => {
            return Promise.reject(this.handleError(error));
        });
    }
    /**
     * 创建记忆
     * @param memory - 记忆对象（部分字段）
     * @returns 创建成功的记忆对象
     * @throws {ApiError} 当 API 调用失败时
     */
    async createMemory(memory) {
        try {
            const response = await this.client.post('/memories', memory);
            return response.data;
        }
        catch (error) {
            throw error;
        }
    }
    /**
     * 搜索记忆
     * @param params - 搜索参数
     * @param params.query - 搜索关键词
     * @param params.user_id - 用户 ID
     * @param params.agent_id - 智能体 ID (可选)
     * @param params.limit - 返回结果数量限制 (默认 10)
     * @param params.filters - 过滤条件 (可选)
     * @returns 搜索结果列表
     */
    async searchMemories(params) {
        try {
            const response = await this.client.post('/memories/search', {
                query: params.query,
                user_id: params.user_id,
                agent_id: params.agent_id,
                limit: params.limit || 10,
                filters: params.filters,
            });
            return response.data.results || [];
        }
        catch (error) {
            throw error;
        }
    }
    /**
     * 获取记忆列表
     * @param params - 查询参数
     * @param params.user_id - 用户 ID
     * @param params.agent_id - 智能体 ID (可选)
     * @param params.limit - 每页数量 (默认 50)
     * @param params.offset - 偏移量 (默认 0)
     * @param params.sort_by - 排序字段 (created_at/updated_at)
     * @param params.order - 排序方向 (asc/desc)
     * @returns 记忆列表和总数
     */
    async getMemories(params) {
        try {
            const queryParams = new URLSearchParams();
            queryParams.append('user_id', params.user_id);
            if (params.agent_id)
                queryParams.append('agent_id', params.agent_id);
            if (params.limit)
                queryParams.append('limit', params.limit.toString());
            if (params.offset)
                queryParams.append('offset', params.offset.toString());
            if (params.sort_by)
                queryParams.append('sort_by', params.sort_by);
            if (params.order)
                queryParams.append('order', params.order);
            const response = await this.client.get(`/memories?${queryParams.toString()}`);
            return response.data;
        }
        catch (error) {
            throw error;
        }
    }
    /**
     * 更新记忆
     * @param memoryId - 记忆 ID
     * @param updates - 更新的字段
     * @returns 更新后的记忆对象
     */
    async updateMemory(memoryId, updates) {
        try {
            const response = await this.client.put(`/memories/${memoryId}`, updates);
            return response.data;
        }
        catch (error) {
            throw error;
        }
    }
    /**
     * 删除记忆
     * @param memoryId - 记忆 ID
     */
    async deleteMemory(memoryId) {
        try {
            await this.client.delete(`/memories/${memoryId}`);
        }
        catch (error) {
            throw error;
        }
    }
    /**
     * 健康检查
     * @returns 服务器是否健康
     */
    async healthCheck() {
        try {
            const response = await this.client.get('/health');
            return response.status === 200;
        }
        catch (error) {
            console.error('[PowerMem API] Health check failed:', error);
            return false;
        }
    }
    /**
     * 处理 API 错误
     * @param error - Axios 错误对象
     * @throws {ApiError} 格式化后的错误
     */
    handleError(error) {
        console.error('[PowerMem API] Error:', error);
        // 网络错误
        if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) {
            throw new ApiError('请求超时，请检查网络连接', 0, ApiErrorCode.NETWORK_ERROR, error);
        }
        if (!error.response) {
            throw new ApiError('无法连接到服务器，请检查服务器地址', 0, ApiErrorCode.NETWORK_ERROR, error);
        }
        const status = error.response.status;
        const data = error.response.data;
        // 根据 HTTP 状态码返回友好错误
        switch (status) {
            case 400:
                throw new ApiError(data?.message || '请求参数错误', status, ApiErrorCode.INVALID_REQUEST, data);
            case 401:
                throw new ApiError('API 密钥无效或已过期', status, ApiErrorCode.UNAUTHORIZED, data);
            case 403:
                throw new ApiError('没有访问权限', status, ApiErrorCode.FORBIDDEN, data);
            case 404:
                throw new ApiError('请求的资源不存在', status, ApiErrorCode.NOT_FOUND, data);
            case 429:
                throw new ApiError('请求过于频繁，请稍后再试', status, ApiErrorCode.RATE_LIMIT, data);
            case 500:
            case 502:
            case 503:
            case 504:
                throw new ApiError('服务器错误，请稍后再试', status, ApiErrorCode.SERVER_ERROR, data);
            default:
                throw new ApiError(data?.message || `请求失败 (${status})`, status, ApiErrorCode.SERVER_ERROR, data);
        }
    }
}
