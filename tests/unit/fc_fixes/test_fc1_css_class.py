"""
FC-1: Issue #1178 — CSS class undefined 修复

验证 Features 组件的图标容器 div 不包含 undefined CSS class。
涉及文件: docs/website/src/components/Features/index.tsx:103

NOTE: 这些测试验证的是 JSX 模板行为。由于是前端组件，
测试通过解析源码文件来验证 className 表达式。
"""

import pytest
import re
from pathlib import Path


FEATURES_TSX = Path("docs/website/src/components/Features/index.tsx")
FEATURES_CSS = Path("docs/website/src/components/Features/styles.module.css")

FEATURE_KEYS = ["developer", "intelligent", "multiAgent", "multimodal", "storage"]


class TestFC1CssClassUndefined:
    """FC-1: 修复 CSS class undefined 问题"""

    def _read_tsx(self) -> str:
        """Read the Features component TSX file."""
        assert FEATURES_TSX.exists(), f"File not found: {FEATURES_TSX}"
        return FEATURES_TSX.read_text()

    def _read_css(self) -> str:
        """Read the Features CSS module file."""
        assert FEATURES_CSS.exists(), f"File not found: {FEATURES_CSS}"
        return FEATURES_CSS.read_text()

    # ── AC-1.1: div className 不包含 undefined ────────────────────────

    def test_ac_1_1_no_dynamic_icon_class_lookup(self):
        """
        Given: Features 组件 index.tsx
        When: 检查图标容器 div 的 className 表达式
        Then: 不应使用 styles[`icon-${feature.key}`] 动态查找

        当前代码使用 `${styles[`icon-${feature.key}`]}` 动态查找，
        CSS 中无对应 class，导致 className 包含 undefined。
        """
        content = self._read_tsx()
        # 验证不存在动态 icon class 查找模式
        dynamic_pattern = r"icon-\$\{feature\.key\}"
        assert not re.search(dynamic_pattern, content), \
            "Found dynamic `icon-${feature.key}` lookup — should use static styles.icon only"

    def test_ac_1_1_classname_uses_static_icon(self):
        """
        Given: Features 组件 index.tsx
        When: 检查图标容器 div 的 className
        Then: 应仅使用 styles.icon（静态引用）

        修复后: <div className={styles.icon}>
        """
        content = self._read_tsx()
        # 验证使用 styles.icon 静态引用
        assert "styles.icon" in content, \
            "styles.icon should be used in the component"

    def test_ac_1_1_no_undefined_in_classname(self):
        """
        Given: Features 组件 index.tsx
        When: 检查整个文件
        Then: 不应有模板字面量拼接 styles 动态查找

        当前代码: className={`${styles.icon} ${styles[`icon-${feature.key}`]}`}
        此模式会导致 undefined 出现在 className 中。
        """
        content = self._read_tsx()
        # 检查是否有模板字面量中拼接 styles 动态属性的模式
        template_pattern = r"\$\{styles\[`icon-"
        assert not re.search(template_pattern, content), \
            "Found template literal with dynamic styles lookup — will produce undefined"

    # ── AC-1.2: 所有 5 个 feature 图标正确显示 ────────────────────────

    def test_ac_1_2_all_five_features_defined(self):
        """
        Given: Features 组件 index.tsx
        When: 检查 feature 定义
        Then: 应包含 5 个 feature key: developer, intelligent, multiAgent, multimodal, storage
        """
        content = self._read_tsx()
        for key in FEATURE_KEYS:
            assert key in content, f"Feature key '{key}' not found in component"

    def test_ac_1_2_icon_class_has_styles(self):
        """
        Given: styles.module.css
        When: 检查 .icon class 定义
        Then: .icon class 应包含完整的圆形背景样式
        """
        css = self._read_css()
        assert ".icon" in css, ".icon class not found in CSS module"
        # 验证关键样式属性
        assert "border-radius" in css, ".icon should have border-radius for circular shape"
        assert "background" in css or "background-color" in css, \
            ".icon should have background color"

    # ── AC-1.3: 移动端响应式布局 ──────────────────────────────────────

    def test_ac_1_3_responsive_media_queries(self):
        """
        Given: styles.module.css
        When: 检查响应式样式
        Then: 应包含 @media 查询用于移动端（≤800px）
        """
        css = self._read_css()
        assert "@media" in css, "CSS should contain media queries for responsive layout"
        assert "800px" in css or "max-width" in css, \
            "Should have breakpoint for mobile layout"


class TestFC1NFR:
    """FC-1: NFR 验证"""

    def _read_tsx(self) -> str:
        return FEATURES_TSX.read_text()

    def _read_css(self) -> str:
        return FEATURES_CSS.read_text()

    def test_nfr_1_1_visual_compatibility(self):
        """
        Given: 修复后的 Features 组件
        When: 检查 .icon class 包含完整样式
        Then: 视觉效果不变（NFR-1.1 向后兼容）
        """
        css = self._read_css()
        # .icon class 应包含 display, place-items, border-radius, background, color
        required_props = ["display", "border-radius", "background"]
        for prop in required_props:
            assert prop in css, f".icon class missing '{prop}' — visual will break"

    def test_nfr_1_2_minimal_change(self):
        """
        Given: 修复后的 Features 组件
        When: 检查 className 表达式
        Then: 仅使用 styles.icon，移除动态查找（NFR-1.2 最小变更）
        """
        content = self._read_tsx()
        # 不应有多个 styles[...] 动态查找用于 icon
        dynamic_lookups = re.findall(r"styles\[`icon-\$\{", content)
        assert len(dynamic_lookups) == 0, \
            f"Expected 0 dynamic icon lookups, found {len(dynamic_lookups)}"

    def test_nfr_1_3_responsive_preserved(self):
        """
        Given: 修复后的 styles.module.css
        When: 检查 media queries
        Then: @media (max-width: 800px) 和 (max-width: 480px) 样式不受影响
        """
        css = self._read_css()
        assert "max-width" in css, "Responsive breakpoints should be preserved"
