import { STORAGE_KEYS } from '@/types';
/**
 * Chrome Storage 服务
 * 负责扩展设置的持久化存储
 */
export class StorageService {
    /**
     * 获取扩展设置
     * @returns 当前设置对象
     */
    static async getSettings() {
        try {
            const result = await chrome.storage.local.get(STORAGE_KEYS.SETTINGS);
            if (!result[STORAGE_KEYS.SETTINGS]) {
                // 如果不存在设置，初始化默认设置
                const defaultSettings = this.getDefaultSettings();
                await this.updateSettings(defaultSettings);
                return defaultSettings;
            }
            return result[STORAGE_KEYS.SETTINGS];
        }
        catch (error) {
            console.error('[StorageService] Failed to get settings:', error);
            return this.getDefaultSettings();
        }
    }
    /**
     * 更新扩展设置
     * @param settings - 部分设置对象
     */
    static async updateSettings(settings) {
        try {
            const currentSettings = await this.getSettingsRaw();
            const mergedSettings = this.deepMerge(currentSettings, settings);
            await chrome.storage.local.set({
                [STORAGE_KEYS.SETTINGS]: mergedSettings,
            });
            console.log('[StorageService] Settings updated successfully');
        }
        catch (error) {
            console.error('[StorageService] Failed to update settings:', error);
            throw error;
        }
    }
    /**
     * 初始化默认设置
     * 首次安装时调用
     */
    static async initializeSettings() {
        try {
            const result = await chrome.storage.local.get(STORAGE_KEYS.SETTINGS);
            if (!result[STORAGE_KEYS.SETTINGS]) {
                const defaultSettings = this.getDefaultSettings();
                await chrome.storage.local.set({
                    [STORAGE_KEYS.SETTINGS]: defaultSettings,
                });
                console.log('[StorageService] Default settings initialized');
            }
        }
        catch (error) {
            console.error('[StorageService] Failed to initialize settings:', error);
            throw error;
        }
    }
    /**
     * 获取默认设置
     * @returns 默认设置对象
     */
    static getDefaultSettings() {
        return {
            server: {
                url: 'http://localhost:8000',
                apiKey: '',
                timeout: 10000,
            },
            user: {
                userId: this.generateUserId(),
                defaultAgentId: 'work',
            },
            capture: {
                autoSave: false,
                minSelectionLength: 10,
                includePageContext: true,
            },
            ui: {
                theme: 'auto',
                language: 'en',
            },
            advanced: {
                enableContextualSuggestions: true,
                maxRecentMemories: 50,
            },
        };
    }
    /**
     * 生成唯一用户 ID
     * @returns 用户 ID 字符串
     */
    static generateUserId() {
        return `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }
    /**
     * 清除所有存储数据
     * 仅用于测试和重置
     */
    static async clear() {
        await chrome.storage.local.clear();
    }
    /**
     * 重置为默认设置
     * 清除所有自定义配置
     */
    static async resetSettings() {
        try {
            const defaultSettings = this.getDefaultSettings();
            await chrome.storage.local.set({
                [STORAGE_KEYS.SETTINGS]: defaultSettings,
            });
            console.log('[StorageService] Settings reset to default');
        }
        catch (error) {
            console.error('[StorageService] Failed to reset settings:', error);
            throw error;
        }
    }
    /**
     * 获取原始设置 (内部使用)
     * @returns 当前设置或默认设置
     */
    static async getSettingsRaw() {
        try {
            const result = await chrome.storage.local.get(STORAGE_KEYS.SETTINGS);
            return result[STORAGE_KEYS.SETTINGS] || this.getDefaultSettings();
        }
        catch (error) {
            console.error('[StorageService] Failed to get raw settings:', error);
            return this.getDefaultSettings();
        }
    }
    /**
     * 深度合并对象
     * @param target - 目标对象
     * @param source - 源对象
     * @returns 合并后的对象
     */
    static deepMerge(target, source) {
        const output = { ...target };
        if (this.isObject(target) && this.isObject(source)) {
            Object.keys(source).forEach((key) => {
                const sourceValue = source[key];
                const targetValue = target[key];
                if (this.isObject(sourceValue) && this.isObject(targetValue)) {
                    output[key] = this.deepMerge(targetValue, sourceValue);
                }
                else {
                    output[key] = sourceValue;
                }
            });
        }
        return output;
    }
    /**
     * 判断是否为对象
     * @param item - 待判断项
     * @returns 是否为对象
     */
    static isObject(item) {
        return item && typeof item === 'object' && !Array.isArray(item);
    }
    /**
     * 监听设置变化
     * @param callback - 变化回调函数
     * @returns 取消监听函数
     */
    static onSettingsChange(callback) {
        const listener = (changes, areaName) => {
            if (areaName === 'local' && changes[STORAGE_KEYS.SETTINGS]) {
                const newSettings = changes[STORAGE_KEYS.SETTINGS].newValue;
                callback(newSettings);
            }
        };
        chrome.storage.onChanged.addListener(listener);
        // 返回取消监听函数
        return () => {
            chrome.storage.onChanged.removeListener(listener);
        };
    }
}
