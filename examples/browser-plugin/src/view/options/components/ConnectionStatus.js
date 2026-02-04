import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import './ConnectionStatus.css';
/**
 * 连接状态组件
 * 显示连接测试结果和服务器信息
 */
export function ConnectionStatus({ result, isTesting }) {
    if (isTesting) {
        return (_jsxs("div", { className: "connection-status testing", children: [_jsx("div", { className: "status-icon", children: "\u23F3" }), _jsx("div", { className: "status-message", children: "\u6B63\u5728\u6D4B\u8BD5\u8FDE\u63A5..." })] }));
    }
    if (!result) {
        return (_jsxs("div", { className: "connection-status idle", children: [_jsx("div", { className: "status-icon", children: "\u2139\uFE0F" }), _jsx("div", { className: "status-message", children: "\u70B9\u51FB\"\u6D4B\u8BD5\u8FDE\u63A5\"\u6309\u94AE\u68C0\u67E5\u670D\u52A1\u5668\u72B6\u6001" })] }));
    }
    if (result.success) {
        return (_jsxs("div", { className: "connection-status success", children: [_jsx("div", { className: "status-icon", children: "\u2705" }), _jsxs("div", { className: "status-content", children: [_jsx("div", { className: "status-message", children: "\u8FDE\u63A5\u6210\u529F" }), _jsxs("div", { className: "status-details", children: [result.latency && (_jsxs("div", { className: "detail-item", children: [_jsx("span", { className: "detail-label", children: "\u54CD\u5E94\u65F6\u95F4:" }), _jsxs("span", { className: "detail-value", children: [result.latency, "ms"] })] })), result.serverVersion && (_jsxs("div", { className: "detail-item", children: [_jsx("span", { className: "detail-label", children: "\u670D\u52A1\u5668\u7248\u672C:" }), _jsx("span", { className: "detail-value", children: result.serverVersion })] }))] })] })] }));
    }
    // 失败状态
    return (_jsxs("div", { className: "connection-status failed", children: [_jsx("div", { className: "status-icon", children: "\u274C" }), _jsxs("div", { className: "status-content", children: [_jsx("div", { className: "status-message", children: "\u8FDE\u63A5\u5931\u8D25" }), _jsx("div", { className: "error-message", children: result.error }), result.errorDetails && (_jsxs("div", { className: "error-details", children: [_jsxs("div", { className: "error-type", children: ["\u9519\u8BEF\u7C7B\u578B: ", getErrorTypeLabel(result.errorDetails.type)] }), result.errorDetails.statusCode && (_jsxs("div", { className: "error-code", children: ["HTTP \u72B6\u6001\u7801: ", result.errorDetails.statusCode] }))] }))] })] }));
}
/**
 * 获取错误类型标签
 */
function getErrorTypeLabel(type) {
    switch (type) {
        case 'network':
            return '网络错误';
        case 'auth':
            return '认证错误';
        case 'server':
            return '服务器错误';
        default:
            return '未知错误';
    }
}
