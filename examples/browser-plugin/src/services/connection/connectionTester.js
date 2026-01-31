import { PowerMemClient } from '../api/client';
/**
 * 连接测试器
 * 负责测试 PowerMem 服务器连接
 */
export class ConnectionTester {
    /**
     * 测试连接
     * @param serverUrl - 服务器 URL
     * @param apiKey - API 密钥
     * @returns 测试结果
     */
    static async test(serverUrl, apiKey) {
        const startTime = Date.now();
        try {
            // 创建临时客户端
            const client = new PowerMemClient(serverUrl, apiKey);
            // 测试健康检查
            const isHealthy = await this.testHealthCheck(client);
            if (!isHealthy) {
                return {
                    success: false,
                    error: '健康检查失败',
                    errorDetails: {
                        type: 'server',
                        message: '服务器健康检查失败，请检查服务是否正常运行'
                    }
                };
            }
            // 测试认证
            const isAuthenticated = await this.testAuthentication(client);
            if (!isAuthenticated) {
                return {
                    success: false,
                    error: '认证失败',
                    errorDetails: {
                        type: 'auth',
                        message: 'API Key 无效或未授权',
                        statusCode: 401
                    }
                };
            }
            // 计算响应时间
            const latency = Date.now() - startTime;
            // 获取服务器信息
            const serverInfo = await this.getServerInfo(client);
            return {
                success: true,
                latency,
                serverVersion: serverInfo.version
            };
        }
        catch (error) {
            console.error('[ConnectionTester] Test failed:', error);
            const errorDetails = this.parseError(error);
            return {
                success: false,
                latency: Date.now() - startTime,
                error: errorDetails?.message || '连接测试失败',
                errorDetails
            };
        }
    }
    /**
     * 测试健康检查端点
     * @param client - API 客户端
     * @returns 是否健康
     */
    static async testHealthCheck(client) {
        try {
            return await client.healthCheck();
        }
        catch {
            return false;
        }
    }
    /**
     * 测试认证
     * 尝试获取记忆列表来验证 API Key 是否有效
     * @param client - API 客户端
     * @returns 是否认证成功
     */
    static async testAuthentication(client) {
        try {
            // 使用临时用户 ID 测试认证
            await client.getMemories({
                user_id: 'test-connection',
                limit: 1
            });
            return true;
        }
        catch (error) {
            // 如果是 401 错误，说明认证失败
            if (error.status === 401) {
                return false;
            }
            // 其他错误（如 404）不影响认证测试
            return true;
        }
    }
    /**
     * 获取服务器信息
     * @param client - API 客户端
     * @returns 服务器版本等信息
     */
    static async getServerInfo(client) {
        try {
            // TODO: 如果 PowerMem API 提供版本信息端点，在这里调用
            // 暂时返回默认版本
            return { version: 'Unknown' };
        }
        catch {
            return { version: 'Unknown' };
        }
    }
    /**
     * 解析错误类型
     * @param error - 错误对象
     * @returns 错误详情
     */
    static parseError(error) {
        // 网络错误
        if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
            return {
                type: 'network',
                message: '连接超时，请检查网络连接和服务器地址'
            };
        }
        if (error.code === 'ECONNREFUSED' || error.message?.includes('ECONNREFUSED')) {
            return {
                type: 'network',
                message: '无法连接到服务器，请确认服务器地址和端口'
            };
        }
        if (!error.status) {
            return {
                type: 'network',
                message: '网络连接失败，请检查服务器地址'
            };
        }
        // 认证错误
        if (error.status === 401) {
            return {
                type: 'auth',
                message: 'API Key 无效或已过期',
                statusCode: 401
            };
        }
        if (error.status === 403) {
            return {
                type: 'auth',
                message: '没有访问权限',
                statusCode: 403
            };
        }
        // 服务器错误
        if (error.status >= 500) {
            return {
                type: 'server',
                message: `服务器错误 (${error.status})，请稍后再试`,
                statusCode: error.status
            };
        }
        // 其他错误
        return {
            type: 'unknown',
            message: error.message || '未知错误',
            statusCode: error.status
        };
    }
}
