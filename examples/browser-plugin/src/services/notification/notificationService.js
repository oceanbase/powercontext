/**
 * 通知服务
 * 管理扩展的通知显示
 */
export class NotificationService {
    /**
     * 显示成功通知
     * @param message - 通知内容
     */
    static showSuccess(message) {
        this.show({
            title: 'PowerMem',
            message,
            type: 'basic',
            iconUrl: this.DEFAULT_ICON,
            priority: 1,
        });
    }
    /**
     * 显示错误通知
     * @param message - 错误内容
     */
    static showError(message) {
        this.show({
            title: 'PowerMem Error',
            message,
            type: 'basic',
            iconUrl: this.DEFAULT_ICON,
            priority: 2,
        });
    }
    /**
     * 显示信息通知
     * @param message - 信息内容
     */
    static showInfo(message) {
        this.show({
            title: 'PowerMem',
            message,
            type: 'basic',
            iconUrl: this.DEFAULT_ICON,
            priority: 0,
        });
    }
    /**
     * 显示自定义通知
     * @param options - 通知选项
     */
    static async show(options) {
        try {
            const notificationId = `powermem_${Date.now()}`;
            await chrome.notifications.create(notificationId, {
                type: options.type || 'basic',
                iconUrl: options.iconUrl || this.DEFAULT_ICON,
                title: options.title,
                message: options.message,
                priority: options.priority || 0,
            });
            // 自动清除通知 (5秒后)
            setTimeout(() => {
                this.clear(notificationId);
            }, 5000);
            return notificationId;
        }
        catch (error) {
            console.error('[NotificationService] Failed to show notification:', error);
            throw error;
        }
    }
    /**
     * 清除通知
     * @param notificationId - 通知 ID
     */
    static async clear(notificationId) {
        try {
            await chrome.notifications.clear(notificationId);
            return true;
        }
        catch (error) {
            console.error('[NotificationService] Failed to clear notification:', error);
            return false;
        }
    }
}
NotificationService.DEFAULT_ICON = '/icons/icon128.png';
