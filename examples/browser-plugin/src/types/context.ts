/**
 * 页面上下文
 */
export interface PageContext {
  /** 当前 URL */
  url: string;
  
  /** 页面标题 */
  title: string;
  
  /** 域名 */
  domain: string;
  
  /** 元描述 */
  metaDescription?: string;
  
  /** 关键词 */
  keywords?: string[];
  
  /** 作者 */
  author?: string;
}

/**
 * 选择上下文
 */
export interface SelectionContext {
  /** 选中的文本 */
  text: string;
  
  /** 文本长度 */
  length: number;
  
  /** 周围段落 */
  surroundingParagraph: string;
  
  /** 选择位置 */
  position: {
    x: number;
    y: number;
  };
}
