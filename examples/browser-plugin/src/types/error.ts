/**
 * 扩展错误类型
 */
export enum ExtensionErrorType {
  /** 网络错误 */
  NETWORK_ERROR = 'NETWORK_ERROR',
  
  /** API 错误 */
  API_ERROR = 'API_ERROR',
  
  /** 配置错误 */
  CONFIG_ERROR = 'CONFIG_ERROR',
  
  /** 权限错误 */
  PERMISSION_ERROR = 'PERMISSION_ERROR',
  
  /** 存储错误 */
  STORAGE_ERROR = 'STORAGE_ERROR',
}

/**
 * API 错误代码枚举
 */
export enum ApiErrorCode {
  NETWORK_ERROR = 'NETWORK_ERROR',
  UNAUTHORIZED = 'UNAUTHORIZED',
  FORBIDDEN = 'FORBIDDEN',
  NOT_FOUND = 'NOT_FOUND',
  RATE_LIMIT = 'RATE_LIMIT',
  SERVER_ERROR = 'SERVER_ERROR',
  INVALID_REQUEST = 'INVALID_REQUEST',
}
