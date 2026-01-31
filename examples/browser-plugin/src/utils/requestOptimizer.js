/**
 * API 请求优化器
 * 提供去重、批处理、重试、超时等优化功能
 */
// 请求缓存 Map，用于去重
const requestCache = new Map();
// 请求清理定时器
const cleanupInterval = 30 * 1000; // 30秒清理一次
setInterval(() => {
    requestCache.clear();
}, cleanupInterval);
/**
 * 请求去重
 * 相同的请求在短时间内只执行一次
 *
 * @param requestFn - 请求函数
 * @param key - 请求唯一标识
 * @returns 优化后的请求函数
 *
 * @example
 * ```typescript
 * const optimizedFetch = deduplicate(
 *   () => fetch('/api/data'),
 *   'fetch-data'
 * );
 * ```
 */
export function deduplicate(requestFn, key) {
    return async () => {
        // 检查缓存
        if (requestCache.has(key)) {
            return requestCache.get(key);
        }
        // 创建新请求
        const promise = requestFn().finally(() => {
            // 请求完成后延迟清除缓存
            setTimeout(() => {
                requestCache.delete(key);
            }, 1000);
        });
        requestCache.set(key, promise);
        return promise;
    };
}
/**
 * 请求批处理
 * 将多个请求合并为一个批次执行
 *
 * @param requests - 请求数组
 * @param batchSize - 批次大小
 * @returns 批处理结果
 *
 * @example
 * ```typescript
 * const results = await batch([
 *   () => fetch('/api/1'),
 *   () => fetch('/api/2'),
 *   () => fetch('/api/3'),
 * ], 2);
 * ```
 */
export async function batch(requests, batchSize = 5) {
    const results = [];
    for (let i = 0; i < requests.length; i += batchSize) {
        const batchRequests = requests.slice(i, i + batchSize);
        const batchResults = await Promise.all(batchRequests.map(fn => fn()));
        results.push(...batchResults);
    }
    return results;
}
/**
 * 请求重试
 * 失败时自动重试
 *
 * @param requestFn - 请求函数
 * @param maxRetries - 最大重试次数（默认3次）
 * @param delay - 重试延迟（默认1000ms）
 * @param backoff - 是否使用指数退避（默认true）
 * @returns 带重试的请求函数
 *
 * @example
 * ```typescript
 * const fetchWithRetry = withRetry(
 *   () => fetch('/api/data'),
 *   3,
 *   1000
 * );
 * ```
 */
export function withRetry(requestFn, maxRetries = 3, delay = 1000, backoff = true) {
    return async () => {
        let lastError;
        for (let attempt = 0; attempt <= maxRetries; attempt++) {
            try {
                return await requestFn();
            }
            catch (error) {
                lastError = error;
                // 如果是最后一次尝试，抛出错误
                if (attempt === maxRetries) {
                    throw lastError;
                }
                // 计算延迟时间（指数退避）
                const retryDelay = backoff ? delay * Math.pow(2, attempt) : delay;
                // 等待后重试
                await new Promise(resolve => setTimeout(resolve, retryDelay));
            }
        }
        throw lastError;
    };
}
/**
 * 请求超时控制
 *
 * @param requestFn - 请求函数
 * @param timeout - 超时时间（毫秒）
 * @returns 带超时的请求函数
 *
 * @example
 * ```typescript
 * const fetchWithTimeout = withTimeout(
 *   () => fetch('/api/data'),
 *   5000
 * );
 * ```
 */
export function withTimeout(requestFn, timeout) {
    return async () => {
        return Promise.race([
            requestFn(),
            new Promise((_, reject) => {
                setTimeout(() => {
                    reject(new Error(`Request timeout after ${timeout}ms`));
                }, timeout);
            }),
        ]);
    };
}
/**
 * 请求节流
 * 限制请求的执行频率
 *
 * @param requestFn - 请求函数
 * @param interval - 节流间隔（毫秒）
 * @returns 节流后的请求函数
 */
export function throttle(requestFn, interval = 1000) {
    let lastCall = 0;
    let pending = null;
    return async () => {
        const now = Date.now();
        const timeSinceLastCall = now - lastCall;
        // 如果在节流期内，返回上次的 Promise
        if (timeSinceLastCall < interval && pending) {
            return pending;
        }
        // 执行新请求
        lastCall = now;
        pending = requestFn();
        try {
            return await pending;
        }
        finally {
            pending = null;
        }
    };
}
/**
 * 请求防抖
 * 延迟执行，多次调用只执行最后一次
 *
 * @param requestFn - 请求函数
 * @param delay - 防抖延迟（毫秒）
 * @returns 防抖后的请求函数
 */
export function debounce(requestFn, delay = 500) {
    let timeoutId;
    let resolvers = [];
    return () => {
        return new Promise((resolve, reject) => {
            // 清除之前的定时器
            if (timeoutId !== undefined) {
                clearTimeout(timeoutId);
            }
            // 添加到等待队列
            resolvers.push({ resolve, reject });
            // 设置新的定时器
            timeoutId = window.setTimeout(async () => {
                const currentResolvers = resolvers;
                resolvers = [];
                try {
                    const result = await requestFn();
                    currentResolvers.forEach(r => r.resolve(result));
                }
                catch (error) {
                    currentResolvers.forEach(r => r.reject(error));
                }
            }, delay);
        });
    };
}
/**
 * 组合优化器
 * 可以组合多个优化器
 *
 * @param requestFn - 请求函数
 * @param optimizers - 优化器数组
 * @returns 优化后的请求函数
 *
 * @example
 * ```typescript
 * const optimizedFetch = compose(
 *   () => fetch('/api/data'),
 *   [
 *     fn => withRetry(fn, 3),
 *     fn => withTimeout(fn, 5000),
 *     fn => deduplicate(fn, 'fetch-data'),
 *   ]
 * );
 * ```
 */
export function compose(requestFn, optimizers) {
    return optimizers.reduce((fn, optimizer) => optimizer(fn), requestFn);
}
/**
 * 请求优化器类（静态方法版本）
 * 保持与文档中的 API 一致
 */
export class RequestOptimizer {
    /**
     * 请求去重
     */
    static deduplicate(requestFn, key) {
        return deduplicate(requestFn, key);
    }
    /**
     * 请求批处理
     */
    static batch(requests, batchSize = 5) {
        return batch(requests, batchSize);
    }
    /**
     * 请求重试
     */
    static withRetry(requestFn, maxRetries, delay) {
        return withRetry(requestFn, maxRetries, delay);
    }
    /**
     * 请求超时控制
     */
    static withTimeout(requestFn, timeout) {
        return withTimeout(requestFn, timeout);
    }
    /**
     * 请求节流
     */
    static throttle(requestFn, interval) {
        return throttle(requestFn, interval);
    }
    /**
     * 请求防抖
     */
    static debounce(requestFn, delay) {
        return debounce(requestFn, delay);
    }
    /**
     * 组合优化器
     */
    static compose(requestFn, optimizers) {
        return compose(requestFn, optimizers);
    }
}
