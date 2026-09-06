"""
test_agent_config.py：配置管理单元测试
"""

import os
import tempfile
import json
from pathlib import Path

import pytest

from core.agent_config import load_config, DEFAULTS, _coerce, _deep_merge


class TestLoadConfig:
    """配置加载测试"""

    def test_defaults(self, monkeypatch):
        """不设任何环境变量、也不读仓库 config.yaml 时，应返回完整的默认值"""
        # 仓库 config.yaml 现为真机配置(openai_compatible)，测试必须与磁盘文件解耦：
        # 让 _find_config 返回一个不存在的路径 → 只剩内置默认值
        monkeypatch.setattr(
            "core.agent_config._find_config",
            lambda: Path(tempfile.gettempdir()) / "__miniyu_no_such_config__.yaml",
        )
        cfg = load_config()
        assert cfg["llm"]["provider"] == "deterministic"
        assert cfg["llm"]["supports_vision"] is False
        assert cfg["agent"]["max_steps"] == 15
        assert cfg["agent"]["confirm_high_risk"] is True
        assert cfg["agent"]["history_window"] == 20

    def test_env_override(self):
        """环境变量应覆盖默认值"""
        os.environ["AGENT_LLM_PROVIDER"] = "openai_compatible"
        os.environ["AGENT_LLM_BASE_URL"] = "https://test.api.com/v1"
        os.environ["AGENT_MAX_STEPS"] = "30"
        os.environ["AGENT_LLM_VISION"] = "true"

        try:
            cfg = load_config()
            assert cfg["llm"]["provider"] == "openai_compatible"
            assert cfg["llm"]["base_url"] == "https://test.api.com/v1"
            assert cfg["agent"]["max_steps"] == 30
            assert cfg["llm"]["supports_vision"] is True
        finally:
            del os.environ["AGENT_LLM_PROVIDER"]
            del os.environ["AGENT_LLM_BASE_URL"]
            del os.environ["AGENT_MAX_STEPS"]
            del os.environ["AGENT_LLM_VISION"]


class TestCoerce:
    """类型转换测试"""

    def test_bool_from_string(self):
        assert _coerce("true", bool) is True
        assert _coerce("1", bool) is True
        assert _coerce("yes", bool) is True
        assert _coerce("false", bool) is False
        assert _coerce("0", bool) is False
        assert _coerce(True, bool) is True

    def test_int_from_string(self):
        assert _coerce("42", int) == 42
        assert _coerce(42, int) == 42


class TestDeepMerge:
    """深度合并测试"""

    def test_simple_merge(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert _deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"llm": {"provider": "det", "model": ""}}
        override = {"llm": {"model": "gpt-4"}}
        result = _deep_merge(base, override)
        assert result["llm"]["provider"] == "det"
        assert result["llm"]["model"] == "gpt-4"