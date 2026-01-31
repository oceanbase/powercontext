/**
 * 格式化日期
 * @param date - 日期字符串或对象
 * @returns 格式化后的日期字符串 (如: "2小时前", "昨天", "2024-01-30")
 */
export function formatDate(date) {
    const now = new Date();
    const targetDate = typeof date === 'string' ? new Date(date) : date;
    const diffMs = now.getTime() - targetDate.getTime();
    const diffMinutes = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
    if (diffMinutes < 1) {
        return '刚刚';
    }
    else if (diffMinutes < 60) {
        return `${diffMinutes}分钟前`;
    }
    else if (diffHours < 24) {
        return `${diffHours}小时前`;
    }
    else if (diffDays === 1) {
        return '昨天';
    }
    else if (diffDays < 7) {
        return `${diffDays}天前`;
    }
    else {
        return targetDate.toLocaleDateString('zh-CN', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit'
        });
    }
}
/**
 * 截断文本
 * @param text - 原始文本
 * @param maxLength - 最大长度
 * @param suffix - 省略符号 (默认 "...")
 * @returns 截断后的文本
 */
export function truncateText(text, maxLength, suffix = '...') {
    if (text.length <= maxLength) {
        return text;
    }
    return text.slice(0, maxLength) + suffix;
}
/**
 * 高亮搜索关键词
 * @param text - 原始文本
 * @param keyword - 搜索关键词
 * @returns 包含高亮标记的 HTML 字符串
 */
export function highlightKeyword(text, keyword) {
    if (!keyword) {
        return text;
    }
    const regex = new RegExp(`(${escapeRegExp(keyword)})`, 'gi');
    return text.replace(regex, '<mark>$1</mark>');
}
/**
 * 转义正则表达式特殊字符
 * @param str - 原始字符串
 * @returns 转义后的字符串
 */
function escapeRegExp(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
/**
 * 防抖函数
 * @param fn - 目标函数
 * @param delay - 延迟时间(ms)
 * @returns 防抖后的函数
 */
export function debounce(fn, delay) {
    let timeoutId = null;
    return function (...args) {
        if (timeoutId) {
            clearTimeout(timeoutId);
        }
        timeoutId = setTimeout(() => {
            fn(...args);
            timeoutId = null;
        }, delay);
    };
}
/**
 * 节流函数
 * @param fn - 目标函数
 * @param interval - 时间间隔(ms)
 * @returns 节流后的函数
 */
export function throttle(fn, interval) {
    let lastCall = 0;
    return function (...args) {
        const now = Date.now();
        if (now - lastCall >= interval) {
            lastCall = now;
            fn(...args);
        }
    };
}
/**
 * 获取域名
 * @param url - 完整 URL
 * @returns 域名
 */
export function getDomain(url) {
    try {
        const urlObj = new URL(url);
        return urlObj.hostname;
    }
    catch {
        return '';
    }
}
/**
 * 检查是否为有效 URL
 * @param str - 字符串
 * @returns 是否为有效 URL
 */
export function isValidUrl(str) {
    try {
        new URL(str);
        return true;
    }
    catch {
        return false;
    }
}
