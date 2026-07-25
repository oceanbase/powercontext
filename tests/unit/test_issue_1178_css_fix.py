"""Unit tests for Issue #1178 — CSS class undefined fix (FC-1).

Verifies that the Features component icon div does not contain 'undefined'
in its className after the fix removes the dynamic CSS class lookup.

Uses source code analysis since this is a React/TypeScript component.
"""

import os
import re

import pytest


def _find_features_component():
    """Find the Features/index.tsx file by walking up from this test file."""
    # Try multiple possible locations
    candidates = [
        os.path.join(os.getcwd(), "docs", "website", "src", "components", "Features", "index.tsx"),
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "website", "src", "components", "Features", "index.tsx"),
    ]
    for path in candidates:
        abs_path = os.path.abspath(path)
        if os.path.exists(abs_path):
            return abs_path
    return None


FEATURES_PATH = _find_features_component()


@pytest.fixture
def component_source():
    """Read the Features component source code."""
    if FEATURES_PATH is None:
        pytest.skip("Features component (docs/website/src/components/Features/index.tsx) not found")
    with open(FEATURES_PATH, "r", encoding="utf-8") as f:
        return f.read()


class TestIssue1178CSSFix:
    """FC-1: Features icon div className must not produce 'undefined'."""

    def test_icon_div_does_not_use_dynamic_class_lookup(self, component_source):
        """AC-1.1: No dynamic class lookup pattern in icon div.

        The pattern styles[`icon-${feature.key}`] produces 'undefined' when
        the CSS class doesn't exist in CSS Modules.
        """
        dynamic_pattern = r'styles\[`icon-\$\{feature\.key\}`\]'
        assert not re.search(dynamic_pattern, component_source), (
            "Found dynamic CSS class lookup `styles[`icon-${feature.key}`]` "
            "which produces 'undefined' when the class is not defined in CSS."
        )

    def test_icon_div_uses_static_icon_class(self, component_source):
        """AC-1.2: Icon div should use styles.icon (static class) only."""
        # Look for the icon div pattern — should be just styles.icon, not a template literal with icon-*
        icon_div_pattern = r'<div\s+className=\{styles\.icon\}>'
        assert re.search(icon_div_pattern, component_source), (
            "Expected icon div to use className={styles.icon} (static assignment)."
        )

    def test_no_template_literal_with_icon_prefix(self, component_source):
        """AC-1.3: No template literal with icon-${} in className."""
        template_pattern = r'className=\{`[^`]*icon-\$\{'
        assert not re.search(template_pattern, component_source), (
            "Found template literal with icon-${} in className which may produce undefined."
        )

    def test_five_feature_keys_still_exist(self, component_source):
        """AC-1.4: All 5 feature keys still present in component data."""
        feature_keys = ["developer", "intelligent", "multiAgent", "multimodal", "storage"]
        for key in feature_keys:
            assert f"'{key}'" in component_source or f'"{key}"' in component_source, (
                f"Feature key '{key}' not found in component source."
            )
