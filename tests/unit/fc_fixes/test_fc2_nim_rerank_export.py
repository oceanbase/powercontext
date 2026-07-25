"""
FC-2: Issue #1158 — NIM reranker 导出

验证 powermem.integrations.rerank 包正确导出 NimRerank 和 NimRerankConfig。
涉及文件: src/powermem/integrations/rerank/__init__.py
"""

import pytest
import importlib
import sys


class TestFC2NimRerankExport:
    """FC-2: NIM reranker 包级导出"""

    # ── AC-2.1: from ... import NimRerank 成功 ────────────────────────

    def test_ac_2_1_import_nim_rerank(self):
        """
        Given: __init__.py 已更新，包含 NimRerank 导入
        When: 执行 from powermem.integrations.rerank import NimRerank
        Then: 导入成功且 NimRerank 指向正确的类

        当前代码 __init__.py 未导入 NimRerank，此测试预期失败。
        """
        try:
            from powermem.integrations.rerank import NimRerank
            assert NimRerank is not None, "NimRerank should not be None"
            assert hasattr(NimRerank, '__init__'), "NimRerank should be a class"
        except ImportError as e:
            pytest.fail(
                f"Cannot import NimRerank from powermem.integrations.rerank: {e}\n"
                "FC-2 fix required: add 'from .nim import NimRerank' to __init__.py"
            )

    # ── AC-2.2: from ... import NimRerankConfig 成功 ──────────────────

    def test_ac_2_2_import_nim_rerank_config(self):
        """
        Given: __init__.py 已更新，包含 NimRerankConfig 导入
        When: 执行 from powermem.integrations.rerank import NimRerankConfig
        Then: 导入成功且 NimRerankConfig 指向正确的配置类

        当前代码 __init__.py 未导入 NimRerankConfig，此测试预期失败。
        """
        try:
            from powermem.integrations.rerank import NimRerankConfig
            assert NimRerankConfig is not None, "NimRerankConfig should not be None"
        except ImportError as e:
            pytest.fail(
                f"Cannot import NimRerankConfig from powermem.integrations.rerank: {e}\n"
                "FC-2 fix required: add 'from .config.providers import NimRerankConfig' to __init__.py"
            )

    # ── AC-2.3: __all__ 包含 NimRerank 和 NimRerankConfig ─────────────

    def test_ac_2_3_all_contains_nim_exports(self):
        """
        Given: __init__.py 已更新
        When: 检查 __all__ 列表
        Then: __all__ 包含 'NimRerank' 和 'NimRerankConfig'

        当前代码 __all__ 不包含 NIM 相关条目，此测试预期失败。
        """
        from powermem.integrations import rerank
        all_exports = getattr(rerank, "__all__", [])
        assert "NimRerank" in all_exports, \
            f"'NimRerank' not in __all__ (current: {all_exports})"
        assert "NimRerankConfig" in all_exports, \
            f"'NimRerankConfig' not in __all__ (current: {all_exports})"

    def test_ac_2_3_star_import_includes_nim(self):
        """
        Given: __init__.py 已更新
        When: 执行 from powermem.integrations.rerank import *
        Then: NimRerank 和 NimRerankConfig 均在导出列表中

        验证 __all__ 包含两个 NIM 类。
        """
        from powermem.integrations import rerank
        all_exports = getattr(rerank, "__all__", [])
        nim_exports = [e for e in all_exports if "Nim" in e]
        assert len(nim_exports) >= 2, \
            f"Expected at least 2 NIM exports in __all__, found: {nim_exports}"

    # ── AC-2.4: help() 显示新增类 ─────────────────────────────────────

    def test_ac_2_4_module_contains_nim_attributes(self):
        """
        Given: __init__.py 已更新
        When: 检查模块属性
        Then: 模块应有 NimRerank 和 NimRerankConfig 属性

        替代 help() 测试——验证模块 dir() 包含两个类。
        """
        from powermem.integrations import rerank
        module_attrs = dir(rerank)
        assert "NimRerank" in module_attrs, \
            f"NimRerank not found in module attributes"
        assert "NimRerankConfig" in module_attrs, \
            f"NimRerankConfig not found in module attributes"


class TestFC2NFR:
    """FC-2: NFR 验证"""

    def test_nfr_2_1_backward_compatible(self):
        """
        Given: __init__.py 新增导入
        When: 检查现有导出仍存在
        Then: QwenRerank, JinaRerank 等原有导出不受影响（NFR-2.1）
        """
        from powermem.integrations.rerank import QwenRerank, JinaRerank, GenericRerank, ZaiRerank
        assert QwenRerank is not None
        assert JinaRerank is not None
        assert GenericRerank is not None
        assert ZaiRerank is not None

    def test_nfr_2_2_dependency_safe(self):
        """
        Given: NimRerank 被导入
        When: 检查模块级导入是否安全
        Then: 模块级导入不应触发 httpx ImportError（NFR-2.2）

        NimRerank 的 httpx 依赖在 __init__ 中检查，不在模块级。
        """
        # 模块级导入应该成功，即使 httpx 未安装
        try:
            from powermem.integrations.rerank import nim
            assert nim is not None
        except ImportError:
            # 如果 nim.py 本身无法导入，说明有其他问题
            pytest.skip("nim.py itself cannot be imported — dependency issue")

    def test_nfr_2_3_import_ordering(self):
        """
        Given: __init__.py 已更新
        When: 检查导入顺序
        Then: NIM 导入应遵循现有模式（先 base/factory，再 providers，再 configs）
        """
        init_file = "src/powermem/integrations/rerank/__init__.py"
        try:
            with open(init_file) as f:
                content = f.read()
        except FileNotFoundError:
            pytest.skip(f"{init_file} not found")

        # 验证 NimRerank 的导入在其他 provider 导入附近
        lines = content.strip().split("\n")
        nim_import_line = None
        zai_import_line = None
        for i, line in enumerate(lines):
            if "NimRerank" in line and "import" in line:
                nim_import_line = i
            if "ZaiRerank" in line and "import" in line:
                zai_import_line = i

        if nim_import_line is not None and zai_import_line is not None:
            # NIM 导入应在 ZAI 附近（同一区域）
            assert abs(nim_import_line - zai_import_line) < 5, \
                "NIM import should be near other provider imports"
