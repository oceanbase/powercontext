/**
 * 内存管理器
 * 负责管理浏览器扩展的内存使用，防止内存泄漏
 */
export class MemoryManager {
    constructor() {
        this.memoryCache = new Map();
        this.config = {
            maxMemories: 200,
            cleanupInterval: 5 * 60 * 1000, // 5分钟
            enableMonitoring: import.meta.env.DEV,
        };
        this.startAutoCleanup();
    }
    /**
     * 获取单例实例
     */
    static getInstance() {
        if (!MemoryManager.instance) {
            MemoryManager.instance = new MemoryManager();
        }
        return MemoryManager.instance;
    }
    /**
     * 配置内存管理器
     */
    static configure(config) {
        const instance = MemoryManager.getInstance();
        instance.config = { ...instance.config, ...config };
    }
    /**
     * 清理未使用的缓存
     * 定期调用以释放内存
     */
    static cleanupCache() {
        const instance = MemoryManager.getInstance();
        // 如果缓存大小超过限制，移除最旧的记忆
        if (instance.memoryCache.size > instance.config.maxMemories) {
            const toRemove = instance.memoryCache.size - instance.config.maxMemories;
            const keys = Array.from(instance.memoryCache.keys());
            for (let i = 0; i < toRemove; i++) {
                instance.memoryCache.delete(keys[i]);
            }
            if (instance.config.enableMonitoring) {
                console.log(`[MemoryManager] Cleaned up ${toRemove} old memories`);
            }
        }
    }
    /**
     * 限制内存中的记忆数量
     * 超过限制时移除最旧的记忆
     *
     * @param maxMemories - 最大记忆数
     */
    static limitMemoryCache(maxMemories) {
        const instance = MemoryManager.getInstance();
        instance.config.maxMemories = maxMemories;
        MemoryManager.cleanupCache();
    }
    /**
     * 监控内存使用
     * 仅在开发环境启用
     */
    static monitorMemoryUsage() {
        const instance = MemoryManager.getInstance();
        if (!instance.config.enableMonitoring) {
            return;
        }
        const usage = MemoryManager.getMemoryUsage();
        console.log('[MemoryManager] Memory Usage:', {
            cacheSize: usage.cacheSize,
            percentage: `${usage.percentage.toFixed(2)}%`,
            used: `${(usage.used / 1024).toFixed(2)} KB`,
        });
    }
    /**
     * 获取当前内存使用情况
     *
     * @returns 内存使用对象
     */
    static getMemoryUsage() {
        const instance = MemoryManager.getInstance();
        // 估算缓存使用的内存（粗略估计）
        let estimatedSize = 0;
        instance.memoryCache.forEach(memory => {
            estimatedSize += JSON.stringify(memory).length * 2; // UTF-16，每字符2字节
        });
        const limit = instance.config.maxMemories * 10 * 1024; // 假设每个记忆平均10KB
        const percentage = (estimatedSize / limit) * 100;
        return {
            used: estimatedSize,
            limit,
            percentage: Math.min(percentage, 100),
            cacheSize: instance.memoryCache.size,
        };
    }
    /**
     * 添加记忆到缓存
     *
     * @param memory - 记忆对象
     */
    static addToCache(memory) {
        const instance = MemoryManager.getInstance();
        instance.memoryCache.set(memory.memory_id, memory);
        // 检查是否超过限制
        if (instance.memoryCache.size > instance.config.maxMemories) {
            MemoryManager.cleanupCache();
        }
    }
    /**
     * 从缓存中获取记忆
     *
     * @param id - 记忆ID
     * @returns 记忆对象或undefined
     */
    static getFromCache(id) {
        const instance = MemoryManager.getInstance();
        return instance.memoryCache.get(id);
    }
    /**
     * 从缓存中移除记忆
     *
     * @param id - 记忆ID
     */
    static removeFromCache(id) {
        const instance = MemoryManager.getInstance();
        instance.memoryCache.delete(id);
    }
    /**
     * 清空所有缓存
     */
    static clearCache() {
        const instance = MemoryManager.getInstance();
        instance.memoryCache.clear();
        if (instance.config.enableMonitoring) {
            console.log('[MemoryManager] Cache cleared');
        }
    }
    /**
     * 启动自动清理
     */
    startAutoCleanup() {
        if (this.cleanupTimer) {
            return;
        }
        this.cleanupTimer = window.setInterval(() => {
            MemoryManager.cleanupCache();
            if (this.config.enableMonitoring) {
                MemoryManager.monitorMemoryUsage();
            }
        }, this.config.cleanupInterval);
    }
    /**
     * 停止自动清理
     */
    static stopAutoCleanup() {
        const instance = MemoryManager.getInstance();
        if (instance.cleanupTimer) {
            clearInterval(instance.cleanupTimer);
            instance.cleanupTimer = undefined;
        }
    }
}
// 在 Background Worker 中初始化
if (typeof chrome !== 'undefined' && chrome.runtime) {
    // 配置内存管理器
    MemoryManager.configure({
        maxMemories: 200,
        cleanupInterval: 5 * 60 * 1000,
    });
    // 定期监控（仅开发环境）
    if (import.meta.env.DEV) {
        setInterval(() => {
            MemoryManager.monitorMemoryUsage();
        }, 60 * 1000); // 每分钟
    }
}
