"""Unit tests for Issue #1158 — NIM reranker export (FC-2).

Verifies that NimRerank and NimRerankConfig are properly exported
from powermem.integrations.rerank.__init__.py.
"""

import pytest


class TestIssue1158NimRerankExport:
    """FC-2: NimRerank and NimRerankConfig must be importable from rerank package."""

    def test_import_nim_rerank_from_package(self):
        """AC-2.1: from powermem.integrations.rerank import NimRerank succeeds."""
        from powermem.integrations.rerank import NimRerank
        assert NimRerank is not None

    def test_import_nim_rerank_config_from_package(self):
        """AC-2.2: from powermem.integrations.rerank import NimRerankConfig succeeds."""
        from powermem.integrations.rerank import NimRerankConfig
        assert NimRerankConfig is not None

    def test_all_contains_nim_rerank(self):
        """AC-2.3: __all__ includes 'NimRerank'."""
        import powermem.integrations.rerank as rerank_pkg
        assert hasattr(rerank_pkg, "__all__"), "rerank package missing __all__"
        assert "NimRerank" in rerank_pkg.__all__, (
            "'NimRerank' not found in powermem.integrations.rerank.__all__"
        )

    def test_all_contains_nim_rerank_config(self):
        """AC-2.4: __all__ includes 'NimRerankConfig'."""
        import powermem.integrations.rerank as rerank_pkg
        assert hasattr(rerank_pkg, "__all__"), "rerank package missing __all__"
        assert "NimRerankConfig" in rerank_pkg.__all__, (
            "'NimRerankConfig' not found in powermem.integrations.rerank.__all__"
        )

    def test_star_import_includes_nim(self):
        """AC-2.5: 'from powermem.integrations.rerank import *' exports NimRerank."""
        import powermem.integrations.rerank as rerank_pkg
        all_exports = rerank_pkg.__all__
        assert "NimRerank" in all_exports
        assert "NimRerankConfig" in all_exports

    def test_existing_exports_preserved(self):
        """NFR-2.1: Existing exports are not removed."""
        import powermem.integrations.rerank as rerank_pkg
        expected_existing = [
            "QwenRerank", "JinaRerank", "GenericRerank", "ZaiRerank",
            "QwenRerankConfig", "JinaRerankConfig", "ZaiRerankConfig", "GenericRerankConfig",
        ]
        for name in expected_existing:
            assert name in rerank_pkg.__all__, f"Existing export '{name}' removed from __all__"
