import { ExtensionErrorType, ApiErrorCode } from '@/types';
import { ApiError } from '@/services/api/client';

/**
 * 错误处理器
 */
export class ErrorHandler {
  /**
   * 处理错误
   * @param error - 错误对象
   * @returns 用户友好的错误消息
   */
  static handle(error: Error): string {
    console.error('[ErrorHandler]', error);
    
    if (error instanceof ApiError) {
      return this.handleApiError(error);
    }
    
    if (this.isNetworkError(error)) {
      return '网络连接失败，请检查网络设置';
    }
    
    return this.getUserMessage(error);
  }

  /**
   * 处理 API 错误
   * @param error - API 错误对象
   * @returns 用户友好的错误消息
   */
  private static handleApiError(error: ApiError): string {
    switch (error.code) {
      case ApiErrorCode.NETWORK_ERROR:
        return '无法连接到 PowerMem 服务器，请检查服务器地址和网络连接';
      
      case ApiErrorCode.UNAUTHORIZED:
        return 'API 密钥无效，请在设置中更新密钥';
      
      case ApiErrorCode.FORBIDDEN:
        return '没有访问权限，请检查账户权限';
      
      case ApiErrorCode.NOT_FOUND:
        return '请求的资源不存在';
      
      case ApiErrorCode.RATE_LIMIT:
        return '请求过于频繁，请稍后再试';
      
      case ApiErrorCode.SERVER_ERROR:
        return '服务器错误，请稍后再试';
      
      case ApiErrorCode.INVALID_REQUEST:
        return error.message || '请求参数错误';
      
      default:
        return error.message || '未知错误';
    }
  }

  /**
   * 判断是否为网络错误
   * @param error - 错误对象
   */
  static isNetworkError(error: Error): boolean {
    const message = error.message.toLowerCase();
    return (
      message.includes('network') ||
      message.includes('timeout') ||
      message.includes('econnaborted') ||
      message.includes('econnrefused')
    );
  }

  /**
   * 判断是否为 API 错误
   * @param error - 错误对象
   */
  static isApiError(error: Error): boolean {
    return error instanceof ApiError;
  }

  /**
   * 获取用户友好的错误消息
   * @param error - 错误对象
   * @returns 错误消息
   */
  static getUserMessage(error: Error): string {
    if (error.message) {
      return error.message;
    }
    
    return '操作失败，请重试';
  }

  /**
   * 记录错误日志
   * @param error - 错误对象
   * @param context - 上下文信息
   */
  static log(error: Error, context?: any): void {
    console.error('[ErrorHandler] Error occurred:', {
      error: {
        name: error.name,
        message: error.message,
        stack: error.stack,
      },
      context,
      timestamp: new Date().toISOString(),
    });
  }
}
