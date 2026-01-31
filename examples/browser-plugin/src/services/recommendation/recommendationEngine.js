import { textSimilarity, urlSimilarity } from '@/utils/similarity';
/**
 * 推荐引擎
 * 基于当前页面推荐相关记忆
 */
export class RecommendationEngine {
    /**
     * 获取推荐
     * @param pageContext - 页面上下文
     * @param memories - 记忆列表
     * @param limit - 返回数量限制
     * @returns 推荐结果列表
     */
    static getRecommendations(pageContext, memories, limit = 10) {
        if (!memories || memories.length === 0) {
            return [];
        }
        // 计算每个记忆的相关性分数
        const results = memories.map(memory => {
            const score = this.calculateOverallScore(memory, pageContext);
            const reason = this.generateReason(memory, pageContext);
            return {
                memory,
                score,
                reason,
            };
        });
        // 按分数排序并返回前 N 个
        return results
            .filter(r => r.score > 0.1) // 过滤低分
            .sort((a, b) => b.score - a.score)
            .slice(0, limit);
    }
    /**
     * 计算域名匹配分数
     * @param memoryDomain - 记忆的域名
     * @param currentDomain - 当前页面域名
     * @returns 分数 (0-1)
     */
    static calculateDomainScore(memoryDomain, currentDomain) {
        if (!memoryDomain || !currentDomain) {
            return 0;
        }
        // 完全匹配
        if (memoryDomain === currentDomain) {
            return 1.0;
        }
        // 子域名匹配（例如 www.example.com 和 example.com）
        const memoryParts = memoryDomain.split('.').reverse();
        const currentParts = currentDomain.split('.').reverse();
        let matchingParts = 0;
        const minLength = Math.min(memoryParts.length, currentParts.length);
        for (let i = 0; i < minLength; i++) {
            if (memoryParts[i] === currentParts[i]) {
                matchingParts++;
            }
            else {
                break;
            }
        }
        // 至少匹配主域名（如 .com）才算有关联
        if (matchingParts >= 2) {
            return matchingParts / Math.max(memoryParts.length, currentParts.length);
        }
        return 0;
    }
    /**
     * 计算 URL 相似度
     * @param memoryUrl - 记忆的 URL
     * @param currentUrl - 当前页面 URL
     * @returns 相似度 (0-1)
     */
    static calculateUrlSimilarity(memoryUrl, currentUrl) {
        if (!memoryUrl || !currentUrl) {
            return 0;
        }
        // 完全匹配
        if (memoryUrl === currentUrl) {
            return 1.0;
        }
        // 使用 URL 相似度算法
        return urlSimilarity(memoryUrl, currentUrl);
    }
    /**
     * 计算标题相似度
     * @param memoryTitle - 记忆标题
     * @param currentTitle - 当前页面标题
     * @returns 相似度 (0-1)
     */
    static calculateTitleSimilarity(memoryTitle, currentTitle) {
        if (!memoryTitle || !currentTitle) {
            return 0;
        }
        // 使用文本相似度算法
        return textSimilarity(memoryTitle, currentTitle);
    }
    /**
     * 计算内容相似度
     * @param memoryContent - 记忆内容
     * @param currentTitle - 当前页面标题（用作参考）
     * @returns 相似度 (0-1)
     */
    static calculateContentSimilarity(memoryContent, currentTitle) {
        if (!memoryContent || !currentTitle) {
            return 0;
        }
        // 使用文本相似度算法
        return textSimilarity(memoryContent, currentTitle);
    }
    /**
     * 计算时间衰减系数
     * 最近的记忆权重更高
     * @param createdAt - 创建时间
     * @returns 衰减系数 (0-1)
     */
    static calculateTimeDecay(createdAt) {
        if (!createdAt) {
            return 0.5; // 默认值
        }
        try {
            const memoryDate = new Date(createdAt);
            const now = new Date();
            const daysDiff = (now.getTime() - memoryDate.getTime()) / (1000 * 60 * 60 * 24);
            // 使用指数衰减
            // 30 天内权重较高，之后逐渐降低
            const decay = Math.exp(-daysDiff / 30);
            // 最低保留 0.3 的权重
            return Math.max(0.3, decay);
        }
        catch (error) {
            return 0.5;
        }
    }
    /**
     * 综合计算相关性分数
     * @param memory - 记忆对象
     * @param pageContext - 页面上下文
     * @returns 综合分数 (0-1)
     */
    static calculateOverallScore(memory, pageContext) {
        const { url, title, domain } = pageContext;
        // 提取记忆的元数据
        const memoryUrl = memory.metadata?.url || '';
        const memoryTitle = memory.metadata?.title || '';
        const memoryDomain = memory.metadata?.domain || '';
        const memoryContent = memory.content || '';
        // 各项分数
        const domainScore = this.calculateDomainScore(memoryDomain, domain);
        const urlScore = this.calculateUrlSimilarity(memoryUrl, url);
        const titleScore = this.calculateTitleSimilarity(memoryTitle, title);
        const contentScore = this.calculateContentSimilarity(memoryContent, title);
        const timeDecay = this.calculateTimeDecay(memory.created_at || '');
        // 加权计算综合分数
        // 域名权重最高，URL 次之，标题和内容相似度也重要
        const weightedScore = domainScore * 0.3 +
            urlScore * 0.25 +
            titleScore * 0.2 +
            contentScore * 0.15 +
            0.1; // 基础分数
        // 应用时间衰减
        return weightedScore * timeDecay;
    }
    /**
     * 生成推荐原因
     * @param memory - 记忆对象
     * @param pageContext - 页面上下文
     * @returns 推荐原因字符串
     */
    static generateReason(memory, pageContext) {
        const { url, title, domain } = pageContext;
        const memoryUrl = memory.metadata?.url || '';
        const memoryTitle = memory.metadata?.title || '';
        const memoryDomain = memory.metadata?.domain || '';
        // 相同页面
        if (memoryUrl === url) {
            return 'Same page';
        }
        // 相同域名
        if (memoryDomain === domain) {
            return `Same domain (${domain})`;
        }
        // 相似标题
        const titleSim = this.calculateTitleSimilarity(memoryTitle, title);
        if (titleSim > 0.7) {
            return 'Similar title';
        }
        // 相似域名
        const domainScore = this.calculateDomainScore(memoryDomain, domain);
        if (domainScore > 0.5) {
            return 'Related domain';
        }
        // 默认
        return 'Possibly related';
    }
}
