/**
 * Content Script 入口
 * 注入到所有网页，负责文本选择监听和页面上下文提取
 */
import { TextSelectionHandler } from './textSelection';
import { PageContextExtractor } from './pageContext';
console.log('[PowerMem Content] Script loaded on:', window.location.href);
// 检查是否为有效网页
if (PageContextExtractor.isValidWebPage()) {
    // 初始化文本选择处理器
    const handler = new TextSelectionHandler(10); // 最小 10 字符
    handler.init();
    console.log('[PowerMem Content] Text selection handler initialized');
}
else {
    console.log('[PowerMem Content] Skipping invalid page');
}
