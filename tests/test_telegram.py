"""
Tests for Telegram bot (mocked).
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest import mock
import pytest


class TestTelegramBot:
    """Test Telegram bot functionality."""

    def test_config_telegram(self):
        """TelegramConfig is properly initialized."""
        from active_memory_mcp.core.config import config
        assert hasattr(config, 'telegram')
        assert hasattr(config.telegram, 'bot_token')
        assert hasattr(config.telegram, 'admin_ids')
        assert hasattr(config.telegram, 'allowed_users')

    def test_config_visualization(self):
        """VisualizationConfig is properly initialized."""
        from active_memory_mcp.core.config import config
        assert hasattr(config, 'visualization')
        assert hasattr(config.visualization, 'clustering_method')
        assert hasattr(config.visualization, 'clustering_perplexity')
        assert hasattr(config.visualization, 'heatmap_resolution')
        assert hasattr(config.visualization, 'enable_heatmap')

    def test_bot_module_import(self):
        """Bot module can be imported."""
        try:
            from active_memory_mcp.telegram import main
            assert callable(main)
        except ImportError:
            pytest.skip("aiogram not installed")

    def test_searcher_import(self):
        """HybridSearcher can be imported in bot module."""
        from active_memory_mcp.search.searcher import HybridSearcher
        searcher = HybridSearcher()
        assert searcher is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
