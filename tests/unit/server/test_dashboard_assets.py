from unittest.mock import MagicMock

from server.dashboard_assets import dashboard_assets_available


def test_dashboard_assets_available_requires_index_html():
    dashboard_dir = MagicMock()
    index_file = MagicMock()
    dashboard_dir.__truediv__.return_value = index_file
    dashboard_dir.is_dir.return_value = True
    index_file.is_file.return_value = False

    assert dashboard_assets_available(dashboard_dir) is False

    index_file.is_file.return_value = True

    assert dashboard_assets_available(dashboard_dir) is True
