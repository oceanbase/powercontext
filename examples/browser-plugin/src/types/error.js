/**
 * 扩展错误类型
 */
export var ExtensionErrorType;
(function (ExtensionErrorType) {
    /** 网络错误 */
    ExtensionErrorType["NETWORK_ERROR"] = "NETWORK_ERROR";
    /** API 错误 */
    ExtensionErrorType["API_ERROR"] = "API_ERROR";
    /** 配置错误 */
    ExtensionErrorType["CONFIG_ERROR"] = "CONFIG_ERROR";
    /** 权限错误 */
    ExtensionErrorType["PERMISSION_ERROR"] = "PERMISSION_ERROR";
    /** 存储错误 */
    ExtensionErrorType["STORAGE_ERROR"] = "STORAGE_ERROR";
})(ExtensionErrorType || (ExtensionErrorType = {}));
/**
 * API 错误代码枚举
 */
export var ApiErrorCode;
(function (ApiErrorCode) {
    ApiErrorCode["NETWORK_ERROR"] = "NETWORK_ERROR";
    ApiErrorCode["UNAUTHORIZED"] = "UNAUTHORIZED";
    ApiErrorCode["FORBIDDEN"] = "FORBIDDEN";
    ApiErrorCode["NOT_FOUND"] = "NOT_FOUND";
    ApiErrorCode["RATE_LIMIT"] = "RATE_LIMIT";
    ApiErrorCode["SERVER_ERROR"] = "SERVER_ERROR";
    ApiErrorCode["INVALID_REQUEST"] = "INVALID_REQUEST";
})(ApiErrorCode || (ApiErrorCode = {}));
