"""
test_llm_client.py：LLM 客户端单元测试
"""

import pytest

from core.llm_client import (
    DeterministicBrain,
    OpenAICompatibleClient,
    create_llm_client,
    ChatResponse,
)


class TestDeterministicBrain:
    """离线脑测试"""

    def setup_method(self):
        self.brain = DeterministicBrain()

    def test_provider(self):
        assert self.brain.provider == "deterministic"
        assert self.brain.supports_vision is False

    def test_organize_desktop(self):
        """整理桌面→触发 sort_directory"""
        resp = self.brain.chat([{"role": "user", "content": "帮我整理桌面"}])
        assert resp.finish_reason == "tool_calls"
        assert len(resp.tool_calls) == 1
        assert resp.tool_calls[0]["name"] == "sort_directory"

    def test_disk_space(self):
        """磁盘空间→触发 disk_usage"""
        resp = self.brain.chat([{"role": "user", "content": "C盘还有多少空间"}])
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls[0]["name"] == "disk_usage"

    def test_list_processes(self):
        """查看进程→触发 list_processes"""
        resp = self.brain.chat([{"role": "user", "content": "看看哪些进程在跑"}])
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls[0]["name"] == "list_processes"

    def test_network_status(self):
        """网络状态→触发 network_status"""
        resp = self.brain.chat([{"role": "user", "content": "网络状态怎么样"}])
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls[0]["name"] == "network_status"

    def test_find_large_files(self):
        """查找大文件→触发 find_large_files"""
        resp = self.brain.chat([{"role": "user", "content": "查找大文件"}])
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls[0]["name"] == "find_large_files"

    def test_help_fallback(self):
        """帮助→纯文本回复"""
        resp = self.brain.chat([{"role": "user", "content": "你能做什么"}])
        assert resp.finish_reason == "stop"
        assert "文件操作" in resp.text

    def test_unknown_instruction(self):
        """未知指令→fallback 提示"""
        resp = self.brain.chat([{"role": "user", "content": "今天天气怎么样"}])
        assert resp.finish_reason == "stop"
        assert "帮助" in resp.text

    def test_kill_process_with_pid(self):
        """结束进程→提取 PID"""
        resp = self.brain.chat([{"role": "user", "content": "结束进程 1234"}])
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls[0]["name"] == "kill_process"
        assert resp.tool_calls[0]["arguments"]["pid"] == 1234

    def test_empty_input(self):
        """空输入→提示"""
        resp = self.brain.chat([{"role": "user", "content": ""}])
        assert resp.finish_reason == "stop"
        assert resp.text == "请说点什么？"

    def test_vision_false(self):
        assert self.brain.supports_vision is False


class TestCreateLLMClient:
    """工厂函数测试"""

    def test_deterministic(self):
        cfg = {"llm": {"provider": "deterministic"}}
        client = create_llm_client(cfg)
        assert isinstance(client, DeterministicBrain)

    def test_openai_compatible(self):
        cfg = {"llm": {"provider": "openai_compatible", "base_url": "https://test.com/v1"}}
        client = create_llm_client(cfg)
        assert isinstance(client, OpenAICompatibleClient)

    def test_unknown_provider(self):
        cfg = {"llm": {"provider": "invalid"}}
        with pytest.raises(ValueError):
            create_llm_client(cfg)


class TestChatResponse:
    """响应格式测试"""

    def test_text_response(self):
        resp = ChatResponse(text="你好")
        assert resp.finish_reason == "stop"
        assert bool(resp) is True

    def test_tool_calls_response(self):
        resp = ChatResponse(
            tool_calls=[{"name": "disk_usage", "arguments": {"path": "C:"}}],
            finish_reason="tool_calls",
        )
        assert resp.finish_reason == "tool_calls"
        assert bool(resp) is True

    def test_empty_response(self):
        resp = ChatResponse()
        assert bool(resp) is False


class _FakeResp:
    """最小 HTTP 响应桩：raise_for_status + json()"""

    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TestJSONTextFallback:
    """后端不支持原生 tool_calls、只回 JSON 文本时的兜底"""

    def test_parse_single_object(self):
        parsed = OpenAICompatibleClient._parse_json_toolcalls(
            '{"name": "disk_usage", "arguments": {"path": "C:"}}'
        )
        assert parsed == [{"name": "disk_usage", "arguments": {"path": "C:"}}]

    def test_parse_fenced_json(self):
        parsed = OpenAICompatibleClient._parse_json_toolcalls(
            '```json\n{"name": "create_directory", "arguments": {"path": "C:/tmp"}}\n```'
        )
        assert parsed == [{"name": "create_directory", "arguments": {"path": "C:/tmp"}}]

    def test_parse_array(self):
        parsed = OpenAICompatibleClient._parse_json_toolcalls(
            '[{"name": "a", "arguments": {}}, {"tool": "b", "arguments": {}}]'
        )
        assert parsed == [{"name": "a", "arguments": {}}, {"name": "b", "arguments": {}}]

    def test_parse_function_alias(self):
        parsed = OpenAICompatibleClient._parse_json_toolcalls(
            '{"function": {"name": "sort_directory", "arguments": {"sort_by": "type"}}}'
        )
        assert parsed == [{"name": "sort_directory", "arguments": {"sort_by": "type"}}]

    def test_parse_nonjson_none(self):
        assert OpenAICompatibleClient._parse_json_toolcalls("今天天气不错") is None

    def test_parse_empty_none(self):
        assert OpenAICompatibleClient._parse_json_toolcalls("") is None
        assert OpenAICompatibleClient._parse_json_toolcalls("   ") is None

    def test_chat_parses_fenced_json(self, monkeypatch):
        """chat() 拿到 content 里的 function-call JSON → 兜底成 tool_calls"""
        payload = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": '```json\n{"name": "current_directory", "arguments": {}}\n```',
                },
                "finish_reason": "stop",
            }],
            "usage": None,
        }
        monkeypatch.setattr(
            "core.llm_client.requests.post", lambda *a, **k: _FakeResp(payload)
        )
        client = OpenAICompatibleClient(base_url="https://x.test/v1", api_key="k", model="m")
        resp = client.chat([{"role": "user", "content": "看看当前目录"}])
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls[0]["name"] == "current_directory"
        assert resp.tool_calls[0]["arguments"] == {}


class TestBaseUrlNormalization:
    """base_url 缺 /v1 自动补（可移植性：各提供商写法不一致）"""

    def test_deepseek_appends_v1(self):
        c = OpenAICompatibleClient(base_url="https://api.deepseek.com", api_key="k")
        assert c.base_url == "https://api.deepseek.com/v1"

    def test_already_v1_unchanged(self):
        c = OpenAICompatibleClient(base_url="https://api.deepseek.com/v1", api_key="k")
        assert c.base_url == "https://api.deepseek.com/v1"

    def test_trailing_slash_stripped(self):
        c = OpenAICompatibleClient(base_url="https://x.test/v1/", api_key="k")
        assert c.base_url == "https://x.test/v1"

    def test_empty_defaults_to_openai(self):
        c = OpenAICompatibleClient(base_url="", api_key="k")
        assert c.base_url == "https://api.openai.com/v1"


class TestDashScopeDefaultModel:
    """create_llm_client：百炼没填 model 时默认 qwen-plus"""

    def test_dashscope_model_default_qwen_plus(self):
        c = create_llm_client({
            "llm": {
                "provider": "openai_compatible",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            },
        })
        assert c.model == "qwen-plus"

    def test_other_provider_keeps_openai_default(self):
        c = create_llm_client({
            "llm": {"provider": "openai_compatible", "base_url": "https://api.openai.com/v1"},
        })
        assert c.model == "gpt-4o-mini"