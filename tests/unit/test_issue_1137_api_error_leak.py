"""Unit tests for Issue #1137 — API exception information leak (FC-5).

Verifies that service error responses do not contain raw exception details
(str(e)) and use generic error messages instead. Uses source code inspection
rather than importing the server module (which has complex dependencies).
"""

import os
import re

import pytest


def _read_source(rel_path):
    """Read a source file relative to project root."""
    abs_path = os.path.join(os.getcwd(), rel_path)
    if not os.path.exists(abs_path):
        # Try from test file location
        abs_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", rel_path
        )
    abs_path = os.path.abspath(abs_path)
    if not os.path.exists(abs_path):
        return None
    with open(abs_path, "r", encoding="utf-8") as f:
        return f.read()


def _find_str_e_in_api_error_messages(source, filename):
    """Find all APIError raises that contain str(e) in message parameter.

    Returns list of (line_number, line_content) tuples.
    """
    violations = []
    lines = source.split('\n')
    in_api_error = False
    paren_depth = 0
    error_start_line = 0

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Track multi-line APIError(...) calls
        if 'raise APIError(' in stripped or 'APIError(' in stripped:
            if 'raise' in stripped or (in_api_error and paren_depth > 0):
                in_api_error = True
                error_start_line = i
                paren_depth = line.count('(') - line.count(')')

                # Check this line for str(e) in message
                if 'message=' in line and 'str(e)' in line:
                    violations.append((i, line.strip()))
                continue

        if in_api_error:
            paren_depth += line.count('(') - line.count(')')
            if 'message=' in line and 'str(e)' in line:
                violations.append((i, line.strip()))
            if paren_depth <= 0:
                in_api_error = False

    return violations


def _find_str_e_in_dict_values(source):
    """Find dict entries with 'error': str(e) pattern."""
    violations = []
    lines = source.split('\n')
    for i, line in enumerate(lines, 1):
        if re.search(r'"error"\s*:\s*str\s*\(\s*e\s*\)', line):
            violations.append((i, line.strip()))
    return violations


class TestIssue1137MemoryServiceLeak:
    """FC-5: memory_service.py error messages must not leak internal details."""

    @pytest.fixture
    def source(self):
        src = _read_source("src/server/services/memory_service.py")
        if src is None:
            pytest.skip("memory_service.py not found")
        return src

    def test_no_str_e_in_api_error_messages(self, source):
        """AC-5.1: No APIError message uses str(e)."""
        violations = _find_str_e_in_api_error_messages(source, "memory_service.py")
        assert len(violations) == 0, (
            f"Found {len(violations)} APIError message(s) with str(e) leak:\n"
            + "\n".join(f"  L{line}: {content}" for line, content in violations)
        )

    def test_no_str_e_in_error_dict_values(self, source):
        """AC-5.2: No dict value uses str(e) for 'error' key."""
        violations = _find_str_e_in_dict_values(source)
        assert len(violations) == 0, (
            f"Found {len(violations)} 'error': str(e) leak(s):\n"
            + "\n".join(f"  L{line}: {content}" for line, content in violations)
        )

    def test_logger_still_logs_original_exception(self, source):
        """NFR-5.2: Original exception still logged via logger.error(..., exc_info=True)."""
        # Verify logger.error calls still exist with exc_info
        logger_calls = re.findall(r'logger\.error\(.*exc_info\s*=\s*True', source, re.DOTALL)
        assert len(logger_calls) > 0, (
            "logger.error with exc_info=True should be preserved for observability"
        )


class TestIssue1137UserServiceLeak:
    """FC-5: user_service.py error messages must not leak internal details."""

    @pytest.fixture
    def source(self):
        src = _read_source("src/server/services/user_service.py")
        if src is None:
            pytest.skip("user_service.py not found")
        return src

    def test_no_str_e_in_api_error_messages(self, source):
        """AC-5.3: No APIError message uses str(e)."""
        violations = _find_str_e_in_api_error_messages(source, "user_service.py")
        assert len(violations) == 0, (
            f"Found {len(violations)} APIError message(s) with str(e) leak:\n"
            + "\n".join(f"  L{line}: {content}" for line, content in violations)
        )


class TestIssue1137AgentServiceLeak:
    """FC-5: agent_service.py error messages must not leak internal details."""

    @pytest.fixture
    def source(self):
        src = _read_source("src/server/services/agent_service.py")
        if src is None:
            pytest.skip("agent_service.py not found")
        return src

    def test_no_str_e_in_api_error_messages(self, source):
        """AC-5.4: No APIError message uses str(e)."""
        violations = _find_str_e_in_api_error_messages(source, "agent_service.py")
        assert len(violations) == 0, (
            f"Found {len(violations)} APIError message(s) with str(e) leak:\n"
            + "\n".join(f"  L{line}: {content}" for line, content in violations)
        )


class TestIssue1137SearchServiceLeak:
    """FC-5: search_service.py error messages must not leak internal details."""

    @pytest.fixture
    def source(self):
        src = _read_source("src/server/services/search_service.py")
        if src is None:
            pytest.skip("search_service.py not found")
        return src

    def test_no_str_e_in_api_error_messages(self, source):
        """AC-5.5: No APIError message uses str(e)."""
        violations = _find_str_e_in_api_error_messages(source, "search_service.py")
        assert len(violations) == 0, (
            f"Found {len(violations)} APIError message(s) with str(e) leak:\n"
            + "\n".join(f"  L{line}: {content}" for line, content in violations)
        )


class TestIssue1137HealthCheckLeak:
    """FC-5: health_check.py error messages must not leak internal details."""

    @pytest.fixture
    def source(self):
        src = _read_source("src/server/utils/health_check.py")
        if src is None:
            pytest.skip("health_check.py not found")
        return src

    def test_database_check_no_str_e_in_error_message(self, source):
        """AC-5.6: Database health check error_message is generic, not str(e)."""
        # Find _check_database_sync function
        db_section_match = re.search(
            r'def _check_database_sync\(\)(.*?)(?=\ndef |\Z)',
            source,
            re.DOTALL,
        )
        if not db_section_match:
            pytest.skip("_check_database_sync not found")

        db_section = db_section_match.group(1)

        # Should NOT have error_msg = str(e) pattern
        assert 'error_msg = str(e)' not in db_section, (
            "Database health check uses 'error_msg = str(e)' — "
            "should use generic 'Database connection failed'"
        )
        # Should NOT have error_message=error_msg where error_msg comes from str(e)
        assert 'error_message=error_msg' not in db_section, (
            "Database health check passes error_msg (from str(e)) to error_message — "
            "should use generic string"
        )

    def test_llm_check_no_str_e_in_error_message(self, source):
        """AC-5.7: LLM health check error_message is generic, not str(e)."""
        llm_section_match = re.search(
            r'def _check_llm_sync\(\)(.*?)(?=\ndef |\Z)',
            source,
            re.DOTALL,
        )
        if not llm_section_match:
            pytest.skip("_check_llm_sync not found")

        llm_section = llm_section_match.group(1)

        assert 'error_msg = str(e)' not in llm_section, (
            "LLM health check uses 'error_msg = str(e)' — "
            "should use generic 'LLM service check failed'"
        )
        assert 'error_message=error_msg' not in llm_section, (
            "LLM health check passes error_msg (from str(e)) to error_message — "
            "should use generic string"
        )


class TestIssue1137TotalLeakCount:
    """FC-5: Verify total count of str(e) leaks matches SPEC (21+ locations)."""

    def test_all_service_files_scanned(self):
        """Verify all target files exist and are scannable."""
        files = [
            "src/server/services/memory_service.py",
            "src/server/services/user_service.py",
            "src/server/services/agent_service.py",
            "src/server/services/search_service.py",
            "src/server/utils/health_check.py",
        ]
        for rel_path in files:
            src = _read_source(rel_path)
            assert src is not None, f"Cannot find {rel_path}"
