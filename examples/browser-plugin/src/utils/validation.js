/**
 * 验证 URL 格式
 * @param url - URL 字符串
 * @returns 验证结果
 */
export function validateUrl(url) {
    if (!url || url.trim() === '') {
        return {
            isValid: false,
            error: 'URL 不能为空'
        };
    }
    try {
        const urlObj = new URL(url);
        if (!urlObj.protocol || !urlObj.host) {
            return {
                isValid: false,
                error: 'URL 格式不正确'
            };
        }
        // 仅允许 http 和 https 协议
        if (urlObj.protocol !== 'http:' && urlObj.protocol !== 'https:') {
            return {
                isValid: false,
                error: '仅支持 http 或 https 协议'
            };
        }
        return { isValid: true };
    }
    catch {
        return {
            isValid: false,
            error: 'URL 格式不正确'
        };
    }
}
/**
 * 验证 API Key 格式
 * @param apiKey - API 密钥
 * @returns 验证结果
 */
export function validateApiKey(apiKey) {
    if (!apiKey || apiKey.trim() === '') {
        return {
            isValid: false,
            error: 'API Key 不能为空'
        };
    }
    // API Key 至少 10 个字符
    if (apiKey.length < 10) {
        return {
            isValid: false,
            error: 'API Key 长度至少为 10 个字符'
        };
    }
    return { isValid: true };
}
/**
 * 验证数字范围
 * @param value - 数值
 * @param min - 最小值
 * @param max - 最大值
 * @returns 验证结果
 */
export function validateNumberRange(value, min, max) {
    if (isNaN(value)) {
        return {
            isValid: false,
            error: '必须是有效数字'
        };
    }
    if (value < min) {
        return {
            isValid: false,
            error: `值不能小于 ${min}`
        };
    }
    if (value > max) {
        return {
            isValid: false,
            error: `值不能大于 ${max}`
        };
    }
    return { isValid: true };
}
/**
 * 验证必填字段
 * @param value - 字段值
 * @param fieldName - 字段名称
 * @returns 验证结果
 */
export function validateRequired(value, fieldName) {
    if (value === null || value === undefined || value === '') {
        return {
            isValid: false,
            error: `${fieldName} 不能为空`
        };
    }
    return { isValid: true };
}
/**
 * 验证超时时间
 * @param timeout - 超时时间(ms)
 * @returns 验证结果
 */
export function validateTimeout(timeout) {
    return validateNumberRange(timeout, 1000, 120000);
}
/**
 * 验证最小选择长度
 * @param length - 长度
 * @returns 验证结果
 */
export function validateMinSelectionLength(length) {
    return validateNumberRange(length, 1, 1000);
}
/**
 * 验证最大记忆数
 * @param count - 数量
 * @returns 验证结果
 */
export function validateMaxRecentMemories(count) {
    return validateNumberRange(count, 10, 500);
}
