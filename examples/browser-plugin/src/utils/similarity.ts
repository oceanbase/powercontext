/**
 * 相似度计算工具
 * 提供各种字符串相似度算法
 */

/**
 * 计算两个字符串的余弦相似度
 * 基于词频向量
 * @param str1 - 字符串 1
 * @param str2 - 字符串 2
 * @returns 相似度 (0-1)
 */
export function cosineSimilarity(str1: string, str2: string): number {
  if (!str1 || !str2) {
    return 0;
  }
  
  // 分词并统计词频
  const words1 = tokenize(str1.toLowerCase());
  const words2 = tokenize(str2.toLowerCase());
  
  // 构建词汇表
  const vocabulary = new Set([...words1, ...words2]);
  
  // 构建词频向量
  const vector1 = Array.from(vocabulary).map(word => 
    words1.filter(w => w === word).length
  );
  const vector2 = Array.from(vocabulary).map(word => 
    words2.filter(w => w === word).length
  );
  
  // 计算余弦相似度
  const dotProduct = vector1.reduce((sum, val, i) => sum + val * vector2[i], 0);
  const magnitude1 = Math.sqrt(vector1.reduce((sum, val) => sum + val * val, 0));
  const magnitude2 = Math.sqrt(vector2.reduce((sum, val) => sum + val * val, 0));
  
  if (magnitude1 === 0 || magnitude2 === 0) {
    return 0;
  }
  
  return dotProduct / (magnitude1 * magnitude2);
}

/**
 * 计算 Levenshtein 距离（编辑距离）
 * @param str1 - 字符串 1
 * @param str2 - 字符串 2
 * @returns 编辑距离
 */
export function levenshteinDistance(str1: string, str2: string): number {
  if (!str1) return str2.length;
  if (!str2) return str1.length;
  
  const matrix: number[][] = [];
  
  // 初始化第一行和第一列
  for (let i = 0; i <= str1.length; i++) {
    matrix[i] = [i];
  }
  for (let j = 0; j <= str2.length; j++) {
    matrix[0][j] = j;
  }
  
  // 填充矩阵
  for (let i = 1; i <= str1.length; i++) {
    for (let j = 1; j <= str2.length; j++) {
      if (str1[i - 1] === str2[j - 1]) {
        matrix[i][j] = matrix[i - 1][j - 1];
      } else {
        matrix[i][j] = Math.min(
          matrix[i - 1][j] + 1,     // 删除
          matrix[i][j - 1] + 1,     // 插入
          matrix[i - 1][j - 1] + 1  // 替换
        );
      }
    }
  }
  
  return matrix[str1.length][str2.length];
}

/**
 * 计算标准化的编辑距离相似度
 * @param str1 - 字符串 1
 * @param str2 - 字符串 2
 * @returns 相似度 (0-1)
 */
export function normalizedLevenshteinSimilarity(str1: string, str2: string): number {
  if (!str1 && !str2) return 1;
  if (!str1 || !str2) return 0;
  
  const distance = levenshteinDistance(str1, str2);
  const maxLength = Math.max(str1.length, str2.length);
  
  return 1 - distance / maxLength;
}

/**
 * 提取关键词
 * 简单实现：分词后过滤停用词
 * @param text - 文本
 * @param limit - 返回数量
 * @returns 关键词数组
 */
export function extractKeywords(text: string, limit: number = 10): string[] {
  if (!text) return [];
  
  // 分词
  const words = tokenize(text.toLowerCase());
  
  // 过滤停用词
  const filteredWords = words.filter(word => !isStopWord(word));
  
  // 统计词频
  const wordFreq = new Map<string, number>();
  filteredWords.forEach(word => {
    wordFreq.set(word, (wordFreq.get(word) || 0) + 1);
  });
  
  // 按词频排序
  const sortedWords = Array.from(wordFreq.entries())
    .sort((a, b) => b[1] - a[1])
    .map(entry => entry[0]);
  
  return sortedWords.slice(0, limit);
}

/**
 * 计算 Jaccard 相似度
 * 基于关键词集合
 * @param keywords1 - 关键词集合 1
 * @param keywords2 - 关键词集合 2
 * @returns 相似度 (0-1)
 */
export function jaccardSimilarity(keywords1: string[], keywords2: string[]): number {
  if (!keywords1.length && !keywords2.length) return 1;
  if (!keywords1.length || !keywords2.length) return 0;
  
  const set1 = new Set(keywords1);
  const set2 = new Set(keywords2);
  
  // 计算交集
  const intersection = new Set([...set1].filter(x => set2.has(x)));
  
  // 计算并集
  const union = new Set([...set1, ...set2]);
  
  return intersection.size / union.size;
}

/**
 * 计算综合文本相似度
 * 结合多种算法
 * @param text1 - 文本 1
 * @param text2 - 文本 2
 * @returns 相似度 (0-1)
 */
export function textSimilarity(text1: string, text2: string): number {
  if (!text1 || !text2) return 0;
  if (text1 === text2) return 1;
  
  // 余弦相似度 (权重 0.4)
  const cosine = cosineSimilarity(text1, text2);
  
  // 编辑距离相似度 (权重 0.3)
  const levenshtein = normalizedLevenshteinSimilarity(text1, text2);
  
  // Jaccard 相似度 (权重 0.3)
  const keywords1 = extractKeywords(text1, 20);
  const keywords2 = extractKeywords(text2, 20);
  const jaccard = jaccardSimilarity(keywords1, keywords2);
  
  return cosine * 0.4 + levenshtein * 0.3 + jaccard * 0.3;
}

/**
 * 分词函数
 * 简单实现：按空格和标点符号分割
 * @param text - 文本
 * @returns 词语数组
 */
function tokenize(text: string): string[] {
  if (!text) return [];
  
  // 移除标点符号并分词
  return text
    .replace(/[^\w\s]/g, ' ')
    .split(/\s+/)
    .filter(word => word.length > 0);
}

/**
 * 判断是否为停用词
 * @param word - 词语
 * @returns 是否为停用词
 */
function isStopWord(word: string): boolean {
  // 常见英文停用词
  const stopWords = new Set([
    'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from',
    'has', 'he', 'in', 'is', 'it', 'its', 'of', 'on', 'that', 'the',
    'to', 'was', 'will', 'with', 'this', 'but', 'they', 'have', 'had',
    'what', 'when', 'where', 'who', 'which', 'why', 'how',
  ]);
  
  return stopWords.has(word) || word.length <= 2;
}

/**
 * 计算 URL 相似度
 * 基于 URL 路径和域名
 * @param url1 - URL 1
 * @param url2 - URL 2
 * @returns 相似度 (0-1)
 */
export function urlSimilarity(url1: string, url2: string): number {
  if (!url1 || !url2) return 0;
  if (url1 === url2) return 1;
  
  try {
    const u1 = new URL(url1);
    const u2 = new URL(url2);
    
    // 域名完全匹配权重更高
    if (u1.hostname === u2.hostname) {
      // 比较路径
      const pathSimilarity = normalizedLevenshteinSimilarity(u1.pathname, u2.pathname);
      return 0.5 + pathSimilarity * 0.5; // 最低 0.5
    }
    
    // 域名相似度
    const hostSimilarity = normalizedLevenshteinSimilarity(u1.hostname, u2.hostname);
    return hostSimilarity * 0.5; // 最高 0.5
  } catch (error) {
    // URL 解析失败，使用字符串相似度
    return normalizedLevenshteinSimilarity(url1, url2);
  }
}
