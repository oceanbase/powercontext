"""
FC-5: Issue #1137 — API 异常信息泄露修复

验证所有 API 端点的异常处理不暴露 str(e) 原始异常信息。
涉及文件:
  - src/server/services/memory_service.py (10 处)
  - src/server/services/user_service.py (7 处)
  - src/server/services/agent_service.py (3 处)
  - src/server/services/search_service.py (1 处)
  - src/server/utils/health_check.py (2 处)
"""

import pytest
import re
from pathlib import Path


# ─── 源文件路径 ────────────────────────────────────────────────────────

MEMORY_SERVICE = Path("src/server/services/memory_service.py")
USER_SERVICE = Path("src/server/services/user_service.py")
AGENT_SERVICE = Path("src/server/services/agent_service.py")
SEARCH_SERVICE = Path("src/server/services/search_service.py")
HEALTH_CHECK = Path("src/server/utils/health_check.py")


# ─── 通用消息映射 ─────────────────────────────────────────────────────

EXPECTED_SAFE_MESSAGES = {
    "memory_service": [
        "Failed to ingest observation",
        "Internal server error",
        "Failed to create memory",
        "Failed to get memory",
        "Failed to list memories",
        "Failed to update memory",
        "Failed to delete memory",
        "Failed to analyze memory quality",
    ],
    "user_service": [
        "Failed to get user profile",
        "Failed to add user profile",
        "Failed to update user memory",
        "Failed to get user memories",
        "Failed to delete user memories",
        "Failed to delete user profile",
        "Failed to get profiles",
    ],
    "agent_service": [
        "Failed to get agent memories",
        "Failed to create agent memory",
        "Failed to share memories",
    ],
    "search_service": [
        "Search failed",
    ],
    "health_check": [
        "Database connection failed",
        "LLM service check failed",
    ],
}


class TestFC5NoExceptionLeak:
    """FC-5: 验证 API 响应不暴露原始异常信息"""

    def _read_file(self, path: Path) -> str:
        assert path.exists(), f"File not found: {path}"
        return path.read_text()

    # ── AC-5.1: API 响应 message 不包含 str(e) ───────────────────────

    def test_ac_5_1_memory_service_no_str_e(self):
        """
        Given: memory_service.py 的异常处理
        When: 检查所有 raise APIError 的 message 参数
        Then: message 不包含 str(e) 或 f-string 中的 {str(e)}

        当前代码有 10 处 str(e) 泄露，此测试预期失败。
        """
        content = self._read_file(MEMORY_SERVICE)
        # 查找 message= 参数中包含 str(e) 的模式
        leak_pattern = r'message\s*=\s*f?["\'].*str\(e\).*["\']'
        matches = re.findall(leak_pattern, content)
        assert len(matches) == 0, \
            f"Found {len(matches)} str(e) leaks in memory_service.py message fields:\n" + \
            "\n".join(matches[:5])

    def test_ac_5_1_user_service_no_str_e(self):
        """
        Given: user_service.py 的异常处理
        When: 检查所有 raise APIError 的 message 参数
        Then: message 不包含 str(e)
        """
        content = self._read_file(USER_SERVICE)
        leak_pattern = r'message\s*=\s*f?["\'].*str\(e\).*["\']'
        matches = re.findall(leak_pattern, content)
        assert len(matches) == 0, \
            f"Found {len(matches)} str(e) leaks in user_service.py:\n" + \
            "\n".join(matches)

    def test_ac_5_1_agent_service_no_str_e(self):
        """
        Given: agent_service.py 的异常处理
        When: 检查所有 raise APIError 的 message 参数
        Then: message 不包含 str(e)
        """
        content = self._read_file(AGENT_SERVICE)
        leak_pattern = r'message\s*=\s*f?["\'].*str\(e\).*["\']'
        matches = re.findall(leak_pattern, content)
        assert len(matches) == 0, \
            f"Found {len(matches)} str(e) leaks in agent_service.py:\n" + \
            "\n".join(matches)

    def test_ac_5_1_search_service_no_str_e(self):
        """
        Given: search_service.py 的异常处理
        When: 检查所有 raise APIError 的 message 参数
        Then: message 不包含 str(e)
        """
        content = self._read_file(SEARCH_SERVICE)
        leak_pattern = r'message\s*=\s*f?["\'].*str\(e\).*["\']'
        matches = re.findall(leak_pattern, content)
        assert len(matches) == 0, \
            f"Found {len(matches)} str(e) leaks in search_service.py:\n" + \
            "\n".join(matches)

    # ── AC-5.2: 原始异常仅记录到日志 ─────────────────────────────────

    def test_ac_5_2_logger_still_logs_original_exception(self):
        """
        Given: 各 service 文件的异常处理
        When: 检查 logger.error/logger.exception 调用
        Then: logger 调用中仍包含异常信息（exc_info=True 或直接传 e）

        修复应保留日志中的异常信息，只移除 API 响应中的泄露。
        """
        for file_path in [MEMORY_SERVICE, USER_SERVICE, AGENT_SERVICE, SEARCH_SERVICE]:
            content = self._read_file(file_path)
            # 至少应有一些 logger.error 或 logger.exception 调用
            log_calls = re.findall(r'logger\.(error|exception)\(', content)
            assert len(log_calls) > 0, \
                f"{file_path.name} should have logger.error/exception calls"

    # ── AC-5.3: health check 使用通用消息 ─────────────────────────────

    def test_ac_5_3_health_check_no_raw_exception(self):
        """
        Given: health_check.py 的异常处理
        When: 检查 _check_database_sync() 和 _check_llm_sync() 的 error_message
        Then: error_message 使用通用消息，不包含 str(e)

        当前代码:
          error_msg = str(e)
          if len(error_msg) > 200:
              error_msg = error_msg[:197] + "..."
        仍泄露部分异常内容。
        """
        content = self._read_file(HEALTH_CHECK)
        # 查找 str(e) 赋值给 error_msg 的模式
        leak_pattern = r'error_msg\s*=\s*str\(e\)'
        matches = re.findall(leak_pattern, content)
        assert len(matches) == 0, \
            f"Found {len(matches)} str(e) assignments to error_msg in health_check.py"

    # ── AC-5.4: service 使用通用前缀消息 ──────────────────────────────

    def test_ac_5_4_memory_service_uses_safe_messages(self):
        """
        Given: memory_service.py 修复后
        When: 检查错误消息模式
        Then: 使用通用前缀（如 "Failed to create memory"），不附加 str(e)
        """
        content = self._read_file(MEMORY_SERVICE)
        # 不应有 str(e) 在 error/dict 字面量中
        error_dict_pattern = r'"error"\s*:\s*str\(e\)'
        assert not re.search(error_dict_pattern, content), \
            "memory_service.py still has 'error': str(e) pattern"

    def test_ac_5_4_user_service_uses_safe_messages(self):
        """
        Given: user_service.py 修复后
        When: 检查错误消息模式
        Then: 使用通用前缀
        """
        content = self._read_file(USER_SERVICE)
        leak_pattern = r'message\s*=\s*f?["\'].*str\(e\).*["\']'
        assert not re.search(leak_pattern, content), \
            "user_service.py still has str(e) in message fields"

    def test_ac_5_4_agent_service_uses_safe_messages(self):
        """
        Given: agent_service.py 修复后
        When: 检查错误消息模式
        Then: 使用通用前缀
        """
        content = self._read_file(AGENT_SERVICE)
        leak_pattern = r'message\s*=\s*f?["\'].*str\(e\).*["\']'
        assert not re.search(leak_pattern, content), \
            "agent_service.py still has str(e) in message fields"

    def test_ac_5_4_search_service_uses_safe_messages(self):
        """
        Given: search_service.py 修复后
        When: 检查错误消息模式
        Then: 使用通用前缀
        """
        content = self._read_file(SEARCH_SERVICE)
        leak_pattern = r'message\s*=\s*f?["\'].*str\(e\).*["\']'
        assert not re.search(leak_pattern, content), \
            "search_service.py still has str(e) in message fields"

    # ── AC-5.5: ErrorResponse 结构验证 ────────────────────────────────

    def test_ac_5_5_error_response_structure(self):
        """
        Given: API 错误响应
        When: 检查 APIError 和 ErrorResponse 结构
        Then: 响应符合 ErrorResponse 模型（error.code, error.message, error.details）
        """
        # 验证 ErrorResponse 模型存在
        try:
            from server.models.errors import APIError, ErrorResponse
            assert APIError is not None
            assert ErrorResponse is not None
        except ImportError:
            pytest.skip("Cannot import server error models")


class TestFC5SpecificLeaks:
    """FC-5: 验证 SPEC 中列出的具体泄露点"""

    def _read_file(self, path: Path) -> str:
        assert path.exists(), f"File not found: {path}"
        return path.read_text()

    def test_ac_5_1_batch_ingest_no_str_e(self):
        """
        Given: memory_service.py 的 batch_ingest_observations()
        When: 检查 per-item 错误处理
        Then: "error": str(e) 模式应被移除

        SPEC 指出 memory_service.py:368 有 "error": str(e) 泄露。
        """
        content = self._read_file(MEMORY_SERVICE)
        error_dict_pattern = r'"error"\s*:\s*str\(e\)'
        matches = re.findall(error_dict_pattern, content)
        assert len(matches) == 0, \
            f"Found {len(matches)} 'error': str(e) patterns in memory_service.py"

    def test_ac_5_3_health_check_database_no_raw_str(self):
        """
        Given: health_check.py:131 — _check_database_sync()
        When: 检查 DependencyStatus 的 error_message
        Then: 应使用 "Database connection failed" 而非 str(e)
        """
        content = self._read_file(HEALTH_CHECK)
        # 不应有 error_msg = str(e) 模式
        assert "error_msg = str(e)" not in content, \
            "health_check.py still uses error_msg = str(e)"

    def test_ac_5_3_health_check_llm_no_raw_str(self):
        """
        Given: health_check.py:224 — _check_llm_sync()
        When: 检查 DependencyStatus 的 error_message
        Then: 应使用 "LLM service check failed" 而非 str(e)
        """
        content = self._read_file(HEALTH_CHECK)
        # 验证没有 str(e) 赋值
        assert "error_msg = str(e)" not in content, \
            "health_check.py still uses error_msg = str(e) for LLM check"


class TestFC5NFR:
    """FC-5: NFR 验证"""

    def _read_file(self, path: Path) -> str:
        assert path.exists(), f"File not found: {path}"
        return path.read_text()

    def test_nfr_5_1_no_sensitive_info_in_api_response(self):
        """
        Given: 所有 service 文件
        When: 检查异常处理
        Then: API 响应不暴露数据库连接字符串、内部路径、堆栈跟踪（NFR-5.1）

        通过检查 str(e) 不出现在 message/error 字段中来验证。
        """
        for file_path in [MEMORY_SERVICE, USER_SERVICE, AGENT_SERVICE, SEARCH_SERVICE, HEALTH_CHECK]:
            content = self._read_file(file_path)
            # message 字段中不应有 str(e)
            msg_leak = re.findall(r'message\s*=\s*f?["\'].*str\(e\).*["\']', content)
            # error dict 中不应有 str(e)
            err_leak = re.findall(r'"error"\s*:\s*str\(e\)', content)
            # error_msg 不应直接等于 str(e)
            em_leak = re.findall(r'error_msg\s*=\s*str\(e\)', content)
            total = len(msg_leak) + len(err_leak) + len(em_leak)
            assert total == 0, \
                f"{file_path.name} has {total} sensitive info leaks"

    def test_nfr_5_2_observability_preserved(self):
        """
        Given: 修复后的 service 文件
        When: 检查 logger 调用
        Then: 原始异常信息仍通过 logger.error/exception 记录（NFR-5.2）
        """
        for file_path in [MEMORY_SERVICE, USER_SERVICE, AGENT_SERVICE, SEARCH_SERVICE]:
            content = self._read_file(file_path)
            # 至少应有 logger.error 或 logger.exception 调用
            has_logging = bool(re.search(r'logger\.(error|exception)\(', content))
            assert has_logging, \
                f"{file_path.name} should still have logger.error/exception calls"

    def test_nfr_5_3_backward_compatible_structure(self):
        """
        Given: 修复后的 service 文件
        When: 检查 APIError 构造
        Then: APIError 结构不变（code, message, status_code）（NFR-5.3）
        """
        content = self._read_file(MEMORY_SERVICE)
        # APIError 构造应仍使用 code=, message=, status_code=
        has_api_error = bool(re.search(r'APIError\(', content))
        if has_api_error:
            # 验证基本结构
            assert "code=" in content or "ErrorCode" in content, \
                "APIError should still use code= parameter"
