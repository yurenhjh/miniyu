"""
test_model_switch.py：Web 模型切换相关单元测试

覆盖：
- set_config_model：只改 llm.model 行、保留注释与其它字段、可重复切换
- _is_chat_model / _filter_chat_models：过滤非对话模型
- list_available_models / probe_model 缺凭据时不发网络请求、返回清晰错误
"""

from pathlib import Path

import pytest

from core import agent_config
from core.agent_config import set_config_model, DEFAULTS
from core.llm_client import (
    _is_chat_model,
    _filter_chat_models,
    list_available_models,
    probe_model,
)

# 一份带中文注释、多字段的假 config.yaml（绝不包含真实 key）
SAMPLE_CONFIG = """\
# ============================================================
# 第4组 miniyu 测试配置
# 优先级：环境变量 > 此文件 > 内置默认值
# ============================================================

llm:
  # 提供商：deterministic / openai_compatible
  provider: openai_compatible

  # 常见 base_url
  base_url: "https://api.example.com/v1"
  api_key: "sk-test-not-a-real-key-12345"
  model: "qwen3.5-plus"

  # 模型是否支持视觉
  supports_vision: false

  timeout: 60
  max_total_tokens: 0

agent:
  max_steps: 15
"""


class TestSetConfigModel:
    def test_updates_only_model_line(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_CONFIG, encoding="utf-8")
        monkeypatch.setattr(agent_config, "_find_config", lambda: cfg_file)

        set_config_model("qwen-max")

        text = cfg_file.read_text(encoding="utf-8")
        # model 行已更新，且仍带引号
        assert 'model: "qwen-max"' in text
        # 中文注释、provider / base_url / api_key / supports_vision 全部保留
        assert "提供商：deterministic / openai_compatible" in text
        assert "provider: openai_compatible" in text
        assert 'base_url: "https://api.example.com/v1"' in text
        assert 'api_key: "sk-test-not-a-real-key-12345"' in text
        assert "supports_vision: false" in text
        assert "max_steps: 15" in text

    def test_can_switch_multiple_times(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(SAMPLE_CONFIG, encoding="utf-8")
        monkeypatch.setattr(agent_config, "_find_config", lambda: cfg_file)

        set_config_model("qwen-turbo")
        set_config_model("qwen-max")

        text = cfg_file.read_text(encoding="utf-8")
        assert 'model: "qwen-max"' in text
        assert "qwen-turbo" not in text

    def test_unquoted_model_value_also_replaced(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            SAMPLE_CONFIG.replace('model: "qwen3.5-plus"', "model: qwen3.5-plus"),
            encoding="utf-8",
        )
        monkeypatch.setattr(agent_config, "_find_config", lambda: cfg_file)

        set_config_model("qwen-plus")
        assert 'model: "qwen-plus"' in cfg_file.read_text(encoding="utf-8")

    def test_no_llm_section_raises(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("agent:\n  max_steps: 15\n", encoding="utf-8")
        monkeypatch.setattr(agent_config, "_find_config", lambda: cfg_file)
        with pytest.raises(ValueError):
            set_config_model("qwen-max")

    def test_missing_file_raises(self, monkeypatch):
        monkeypatch.setattr(
            agent_config, "_find_config", lambda: Path("Z:/no/such/config.yaml")
        )
        with pytest.raises(FileNotFoundError):
            set_config_model("qwen-max")


class TestChatModelFilter:
    def test_is_chat_model(self):
        assert _is_chat_model("qwen3.5-plus") is True
        assert _is_chat_model("qwen-vl-max") is True
        assert _is_chat_model("qwen2.5-72b-instruct") is True
        assert _is_chat_model("text-embedding-v3") is False
        assert _is_chat_model("gte-rerank") is False
        assert _is_chat_model("wanx-v1") is False

    def test_filter_chat_models(self):
        ids = [
            "wanx-v1",
            "qwen-plus",
            "text-embedding-v3",
            "gte-rerank",
            "qwen2.5-72b-instruct",
            "qwen-plus",
            "qwen-vl-max",
        ]
        result = _filter_chat_models(ids)
        assert result == ["qwen-plus", "qwen-vl-max", "qwen2.5-72b-instruct"]
        assert "wanx-v1" not in result
        assert "text-embedding-v3" not in result


class TestNoCredentials:
    """缺凭据时必须立刻返回错误，绝不发起真实网络请求"""

    def test_list_models_needs_creds(self):
        result = list_available_models({"llm": {"provider": "openai_compatible"}})
        assert result["models"] == []
        assert "base_url" in result["error"] or "api_key" in result["error"]

    def test_list_models_ignores_deterministic_defaults(self):
        # 即使提供默认值里的空字段也只会报错
        result = list_available_models(DEFAULTS)
        assert result["models"] == []

    def test_probe_needs_creds(self):
        result = probe_model({"llm": {"provider": "openai_compatible"}}, "qwen-max")
        assert result["ok"] is False
        assert "未配置" in result["message"]

    def test_probe_needs_model(self):
        result = probe_model(
            {"llm": {"base_url": "https://api.example.com/v1", "api_key": "k"}},
            "",
        )
        assert result["ok"] is False
        assert "未指定" in result["message"]

    def test_default_probe_fails_cleanly(self):
        result = probe_model(DEFAULTS, "qwen-max")
        assert result["ok"] is False
