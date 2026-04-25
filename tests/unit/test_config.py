"""Unit tests for fund-administration-service config compliance.

Verifies that config uses UnifiedCloudConfig / BaseConfig from unified_trading_library.config_interface
and does NOT use os.getenv() or os.environ directly.
"""

import ast
from pathlib import Path

import pytest


class TestConfigCompliance:
    """Assert fund-administration-services config follows coding standards."""

    def test_service_config_no_os_getenv(self):
        """service_config.py must not call os.getenv() or os.environ."""
        source_root = Path(__file__).parent.parent.parent / "fund_administration_service"
        violations: list[str] = []

        # Files that legitimately use os.environ for bootstrap config or runtime compat (codex exception)
        # mock_data_provider.py reads WORKSPACE_ROOT (local filesystem path, not cloud config)
        allowed_environ_files = {
            "event_loop.py",
            "kill_switch.py",
            "main.py",
            "mock_data_provider.py",
        }

        for py_file in source_root.rglob("*.py"):
            if "test" in py_file.name:
                continue
            if py_file.name in allowed_environ_files:
                continue
            try:
                text = py_file.read_text(encoding="utf-8")
            except OSError:
                continue
            # Use AST call-site analysis to avoid false positives from docstrings
            # and comments that mention os.getenv() as a "do NOT use" example.
            try:
                tree = ast.parse(text)
            except SyntaxError:
                # Parse failure: fall back to raw text scan (conservative)
                if "os.getenv" in text or "os.environ" in text:
                    violations.append(str(py_file.relative_to(source_root)))
                continue
            has_violation = False
            for node in ast.walk(tree):
                # Detect: os.getenv(...) or os.environ[...] or os.environ.get(...)
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "os"
                    and node.attr in ("getenv", "environ")
                ):
                    has_violation = True
                    break
            if has_violation:
                violations.append(str(py_file.relative_to(source_root)))

        assert not violations, (
            f"os.getenv()/os.environ found in production source — use UnifiedCloudConfig instead: {violations}"
        )

    def test_config_module_importable(self):
        """Config module must be importable without raising."""
        import fund_administration_service.config

    def test_get_service_config_returns_value(self):
        """get_service_config() must return a non-None config object."""
        from fund_administration_service.config import get_service_config

        config = get_service_config()
        assert config is not None

    def test_config_has_gcp_project_id(self):
        """Config must expose gcp_project_id (not GCP_PROJECT_ID)."""
        from fund_administration_service.config import get_service_config

        config = get_service_config()
        assert hasattr(config, "gcp_project_id"), (
            "Config must have gcp_project_id attribute (not GCP_PROJECT_ID)"
        )
