import { sendMessage } from '@/utils/messaging';
/**
 * 记忆服务
 * 封装记忆的 CRUD 操作
 */
export class MemoryService {
    /**
     * 创建记忆
     * @param memory - 记忆数据
     * @returns 创建的记忆
     */
    static async create(memory) {
        try {
            const response = await sendMessage({
                type: 'SAVE_MEMORY',
                payload: memory,
            });
            if (!response.success) {
                throw new Error(response.error || 'Failed to create memory');
            }
            return response.data;
        }
        catch (error) {
            console.error('[MemoryService] Failed to create memory:', error);
            throw error;
        }
    }
    /**
     * 更新记忆
     * @param memoryId - 记忆 ID
     * @param updates - 更新字段
     * @returns 更新后的记忆
     */
    static async update(memoryId, updates) {
        try {
            const response = await sendMessage({
                type: 'UPDATE_MEMORY',
                payload: {
                    memoryId,
                    updates,
                },
            });
            if (!response.success) {
                throw new Error(response.error || 'Failed to update memory');
            }
            return response.data;
        }
        catch (error) {
            console.error('[MemoryService] Failed to update memory:', error);
            throw error;
        }
    }
    /**
     * 删除记忆
     * @param memoryId - 记忆 ID
     * @param skipConfirm - 跳过确认对话框
     */
    static async delete(memoryId, skipConfirm = false) {
        try {
            // 显示确认对话框
            if (!skipConfirm) {
                const confirmed = await this.confirmDelete(memoryId);
                if (!confirmed) {
                    return;
                }
            }
            const response = await sendMessage({
                type: 'DELETE_MEMORY',
                payload: { memoryId },
            });
            if (!response.success) {
                throw new Error(response.error || 'Failed to delete memory');
            }
            console.log('[MemoryService] Memory deleted:', memoryId);
        }
        catch (error) {
            console.error('[MemoryService] Failed to delete memory:', error);
            throw error;
        }
    }
    /**
     * 批量删除记忆
     * @param memoryIds - 记忆 ID 数组
     * @param skipConfirm - 跳过确认对话框
     */
    static async batchDelete(memoryIds, skipConfirm = false) {
        try {
            // 显示确认对话框
            if (!skipConfirm) {
                const confirmed = await this.confirmBatchDelete(memoryIds.length);
                if (!confirmed) {
                    return;
                }
            }
            // 逐个删除
            const deletePromises = memoryIds.map(id => sendMessage({
                type: 'DELETE_MEMORY',
                payload: { memoryId: id },
            }));
            const responses = await Promise.all(deletePromises);
            // 检查是否有失败的删除
            const failures = responses.filter(r => !r.success);
            if (failures.length > 0) {
                throw new Error(`Failed to delete ${failures.length} memories`);
            }
            console.log('[MemoryService] Batch deleted:', memoryIds.length);
        }
        catch (error) {
            console.error('[MemoryService] Failed to batch delete memories:', error);
            throw error;
        }
    }
    /**
     * 获取记忆详情
     * @param memoryId - 记忆 ID
     * @returns 记忆对象
     */
    static async getById(memoryId) {
        try {
            const response = await sendMessage({
                type: 'GET_MEMORY',
                payload: { memoryId },
            });
            if (!response.success) {
                throw new Error(response.error || 'Failed to get memory');
            }
            return response.data;
        }
        catch (error) {
            console.error('[MemoryService] Failed to get memory:', error);
            throw error;
        }
    }
    /**
     * 显示删除确认对话框
     * @param memoryId - 记忆 ID
     * @returns 是否确认删除
     */
    static async confirmDelete(memoryId) {
        // 检查是否设置了"不再提示"
        const result = await chrome.storage.local.get('skipDeleteConfirm');
        if (result.skipDeleteConfirm) {
            return true;
        }
        return new Promise((resolve) => {
            // 这里应该显示确认对话框
            // 暂时返回 true，实际实现需要使用 DeleteConfirmDialog 组件
            // TODO: Implement confirmation dialog
            const confirmed = window.confirm('Are you sure you want to delete this memory?');
            resolve(confirmed);
        });
    }
    /**
     * 显示批量删除确认对话框
     * @param count - 要删除的数量
     * @returns 是否确认删除
     */
    static async confirmBatchDelete(count) {
        // 检查是否设置了"不再提示"
        const result = await chrome.storage.local.get('skipDeleteConfirm');
        if (result.skipDeleteConfirm) {
            return true;
        }
        return new Promise((resolve) => {
            // 这里应该显示确认对话框
            // 暂时返回 true，实际实现需要使用 DeleteConfirmDialog 组件
            // TODO: Implement confirmation dialog
            const confirmed = window.confirm(`Are you sure you want to delete ${count} memories?`);
            resolve(confirmed);
        });
    }
}
