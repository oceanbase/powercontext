import type { PageContext, SelectionContext } from '@/types';

/**
 * 页面上下文提取器
 * 负责从当前页面提取结构化信息
 */
export class PageContextExtractor {
  /**
   * 获取完整页面上下文
   * @returns 页面上下文对象
   */
  static getFullContext(): PageContext {
    const url = window.location.href;
    const title = document.title || '';
    const domain = window.location.hostname;
    const metadata = this.extractMetadata();

    return {
      url,
      title,
      domain,
      metaDescription: metadata.description,
      keywords: metadata.keywords?.split(',').map(k => k.trim()).filter(Boolean),
      author: metadata.author,
    };
  }

  /**
   * 获取选中文本的上下文
   * @param selectedText - 选中的文本
   * @returns 选择上下文对象
   */
  static getSelectionContext(selectedText: string): SelectionContext {
    const selection = window.getSelection();
    
    if (!selection || selection.rangeCount === 0) {
      return {
        text: selectedText,
        length: selectedText.length,
        surroundingParagraph: '',
        position: { x: 0, y: 0 },
      };
    }

    const range = selection.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    const surroundingParagraph = this.getSurroundingParagraph(selection);

    return {
      text: selectedText,
      length: selectedText.length,
      surroundingParagraph,
      position: {
        x: rect.left + window.scrollX,
        y: rect.top + window.scrollY,
      },
    };
  }

  /**
   * 提取页面元信息
   * @returns 元信息对象
   */
  private static extractMetadata(): {
    description?: string;
    keywords?: string;
    author?: string;
    [key: string]: string | undefined;
  } {
    const metadata: Record<string, string | undefined> = {};

    // 提取 description
    const descMeta = document.querySelector('meta[name="description"]') as HTMLMetaElement;
    if (descMeta) {
      metadata.description = descMeta.content;
    }

    // 提取 keywords
    const keywordsMeta = document.querySelector('meta[name="keywords"]') as HTMLMetaElement;
    if (keywordsMeta) {
      metadata.keywords = keywordsMeta.content;
    }

    // 提取 author
    const authorMeta = document.querySelector('meta[name="author"]') as HTMLMetaElement;
    if (authorMeta) {
      metadata.author = authorMeta.content;
    }

    // 提取 Open Graph 数据
    const ogDescription = document.querySelector('meta[property="og:description"]') as HTMLMetaElement;
    if (ogDescription && !metadata.description) {
      metadata.description = ogDescription.content;
    }

    return metadata;
  }

  /**
   * 获取选中文本周围的段落
   * @param selection - Selection 对象
   * @returns 段落文本
   */
  private static getSurroundingParagraph(selection: Selection): string {
    try {
      const range = selection.getRangeAt(0);
      let container = range.commonAncestorContainer;

      // 如果是文本节点，找到父元素
      if (container.nodeType === Node.TEXT_NODE) {
        container = container.parentElement || container;
      }

      // 向上查找段落元素
      let paragraph: Element | null = null;
      let current = container as Element;
      
      while (current && current !== document.body) {
        const tagName = current.tagName?.toLowerCase();
        if (tagName === 'p' || tagName === 'div' || tagName === 'article' || 
            tagName === 'section' || tagName === 'li') {
          paragraph = current;
          break;
        }
        current = current.parentElement as Element;
      }

      if (paragraph) {
        const text = paragraph.textContent || '';
        // 限制长度，避免返回过长的文本
        return text.length > 500 ? text.substring(0, 500) + '...' : text;
      }

      return '';
    } catch (error) {
      console.error('[PageContextExtractor] Failed to get surrounding paragraph:', error);
      return '';
    }
  }

  /**
   * 判断是否为有效的网页
   * 排除 chrome:// 等特殊页面
   * @returns 是否为有效网页
   */
  static isValidWebPage(): boolean {
    const url = window.location.href;
    
    // 排除特殊协议
    const invalidProtocols = ['chrome:', 'chrome-extension:', 'about:', 'data:', 'file:'];
    
    for (const protocol of invalidProtocols) {
      if (url.startsWith(protocol)) {
        return false;
      }
    }

    return true;
  }
}
