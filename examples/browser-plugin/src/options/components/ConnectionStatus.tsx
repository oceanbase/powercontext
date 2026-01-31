import React from 'react';
import './ConnectionStatus.css';
import type { ConnectionTestResult } from '../../services/connection/connectionTester';

/**
 * ConnectionStatus 组件属性
 */
interface ConnectionStatusProps {
  /** 测试结果 */
  result: ConnectionTestResult | null;
  
  /** 是否正在测试 */
  isTesting: boolean;
}

/**
 * 连接状态组件
 * 显示连接测试结果和服务器信息
 */
export function ConnectionStatus({ result, isTesting }: ConnectionStatusProps): JSX.Element {
  if (isTesting) {
    return (
      <div className="connection-status testing">
        <div className="status-icon">⏳</div>
        <div className="status-message">正在测试连接...</div>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="connection-status idle">
        <div className="status-icon">ℹ️</div>
        <div className="status-message">点击"测试连接"按钮检查服务器状态</div>
      </div>
    );
  }

  if (result.success) {
    return (
      <div className="connection-status success">
        <div className="status-icon">✅</div>
        <div className="status-content">
          <div className="status-message">连接成功</div>
          <div className="status-details">
            {result.latency && (
              <div className="detail-item">
                <span className="detail-label">响应时间:</span>
                <span className="detail-value">{result.latency}ms</span>
              </div>
            )}
            {result.serverVersion && (
              <div className="detail-item">
                <span className="detail-label">服务器版本:</span>
                <span className="detail-value">{result.serverVersion}</span>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // 失败状态
  return (
    <div className="connection-status failed">
      <div className="status-icon">❌</div>
      <div className="status-content">
        <div className="status-message">连接失败</div>
        <div className="error-message">{result.error}</div>
        {result.errorDetails && (
          <div className="error-details">
            <div className="error-type">
              错误类型: {getErrorTypeLabel(result.errorDetails.type)}
            </div>
            {result.errorDetails.statusCode && (
              <div className="error-code">
                HTTP 状态码: {result.errorDetails.statusCode}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * 获取错误类型标签
 */
function getErrorTypeLabel(type: string): string {
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
