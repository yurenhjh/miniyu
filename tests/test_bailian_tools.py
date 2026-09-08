"""
test_bailian_tools.py
百炼服务端工具（联网搜索 enable_search）单元测试

覆盖：工厂透传 server_tools、请求体注入（chat / chat_stream）、非百炼端点忽略、
400 拒绝自适应停用 + reset 还原、agent 层 browser_search 隐藏/还原与 system 提示注入、
FailoverClient 委托与降级语义。

实测依据见 examples/probe_bailian_search.py（真实 API 探针：混用无冲突、
不开服务端搜索时模型会选本地工具）。
"""

import json
import unittest
from unittest.mock import MagicMock, patch

import requests

from core.agent import Agent
from core.llm_client import (
    ChatResponse,
    FailoverClient,
    OpenAICompatibleClient,
    create_llm_client,
)


BAILIAN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
OLLAMA_URL = "http://localhost:11434/v1"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "sort_directory",
        "description": "整理目录",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
}]


def _bailian_client(**kw):
    return OpenAICompatibleClient(
        base_url=BAILIAN_URL, api_key="k", model="qwen3.6-flash",
        server_tools={"web_search": True}, **kw)


def _http_error(status, message):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"error": {"message": message}}
    err = requests.exceptions.HTTPError(f"{status} error")
    err.response = resp
    return err


def _ok_response(content="ok"):
    return {
        "choices": [{"message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"total_tokens": 5},
    }


def _recording_side_effect(responses):
    """记录每次请求体，按顺序返回响应 mock 或抛错"""
    bodies = []

    def side_effect(url, headers=None, json=None, timeout=None, stream=False):
        bodies.append(json)
        action = responses[min(len(bodies) - 1, len(responses) - 1)]
        if isinstance(action, Exception):
            raise action
        if hasattr(action, "iter_content"):
            return action
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status.return_value = None
        mock.json.return_value = action
        return mock

    return side_effect, bodies


def _sse_response(dicts):
    mock = MagicMock()
    mock.status_code = 200
    mock.raise_for_status.return_value = None
    chunks = [f"data: {json.dumps(d)}\n".encode() for d in dicts]
    chunks.append(b"data: [DONE]\n")
    mock.iter_content.return_value = chunks
    return mock


class TestFactoryAndFlags(unittest.TestCase):
    """配置工厂与生效条件（顶层 web_search.enabled 总开关）"""

    def test_factory_passes_web_search_enabled(self):
        cfg = {"llm": {
            "provider": "openai_compatible", "base_url": BAILIAN_URL,
            "api_key": "k", "model": "qwen3.6-flash",
        }, "web_search": {"enabled": True}}
        c = create_llm_client(cfg)
        self.assertTrue(c.server_web_search_active)

    def test_factory_disabled_by_master_switch(self):
        cfg = {"llm": {
            "provider": "openai_compatible", "base_url": BAILIAN_URL,
            "api_key": "k", "model": "qwen3.6-flash",
        }, "web_search": {"enabled": False}}
        c = create_llm_client(cfg)
        self.assertFalse(c.server_web_search_active)

    def test_factory_defaults_to_enabled(self):
        cfg = {"llm": {
            "provider": "openai_compatible", "base_url": BAILIAN_URL,
            "api_key": "k", "model": "qwen3.6-flash",
        }}
        c = create_llm_client(cfg)
        self.assertTrue(c.server_web_search_active)

    def test_no_config_inactive(self):
        c = OpenAICompatibleClient(base_url=BAILIAN_URL, api_key="k", model="m")
        self.assertFalse(c.server_web_search_active)

    def test_non_bailian_endpoint_ignored(self):
        c = OpenAICompatibleClient(base_url=OLLAMA_URL, api_key="k", model="m",
                                   server_tools={"web_search": True})
        self.assertFalse(c.server_web_search_active)

    def test_maas_endpoint_recognized(self):
        c = OpenAICompatibleClient(
            base_url="https://ws1.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
            server_tools={"web_search": True})
        self.assertTrue(c.is_bailian)
        self.assertTrue(c.server_web_search_active)


class TestChatInjection(unittest.TestCase):
    """chat() 请求体注入与自适应停用"""

    @patch("core.llm_client.requests.post")
    def test_chat_injects_enable_search_with_tools(self, mock_post):
        """百炼端点 + 开关开启：enable_search 注入，且本地工具照常透传（混用无冲突）"""
        client = _bailian_client()
        side, bodies = _recording_side_effect([_ok_response()])
        mock_post.side_effect = side

        client.chat([{"role": "user", "content": "hi"}], tools=TOOLS)

        self.assertTrue(bodies[0]["enable_search"])
        self.assertEqual(bodies[0]["tools"], TOOLS)

    @patch("core.llm_client.requests.post")
    def test_chat_rejected_then_disabled(self, mock_post):
        """API 拒绝 enable_search → 停注重试，标志生效"""
        client = _bailian_client()
        side, bodies = _recording_side_effect([
            _http_error(400, "enable_search is not supported"),
            _ok_response(),
        ])
        mock_post.side_effect = side

        resp = client.chat([{"role": "user", "content": "hi"}], tools=TOOLS)

        self.assertEqual(resp.text, "ok")
        self.assertEqual(len(bodies), 2)
        self.assertTrue(bodies[0]["enable_search"])
        self.assertNotIn("enable_search", bodies[1])
        self.assertEqual(bodies[1]["tools"], TOOLS)  # 本地工具不受影响
        self.assertTrue(client._server_search_rejected)
        self.assertFalse(client.server_web_search_active)

    @patch("core.llm_client.requests.post")
    def test_non_bailian_no_injection(self, mock_post):
        client = OpenAICompatibleClient(base_url=OLLAMA_URL, api_key="k", model="m",
                                        server_tools={"web_search": True})
        side, bodies = _recording_side_effect([_ok_response()])
        mock_post.side_effect = side

        client.chat([{"role": "user", "content": "hi"}], tools=TOOLS)

        self.assertNotIn("enable_search", bodies[0])

    def test_reset_restores(self):
        client = _bailian_client()
        client._server_search_rejected = True
        self.assertFalse(client.server_web_search_active)
        client.reset_runtime_flags()
        self.assertTrue(client.server_web_search_active)


class TestStreamInjection(unittest.TestCase):
    """chat_stream() 请求体注入与自适应停用"""

    @patch("core.llm_client.requests.post")
    def test_stream_injects_enable_search(self, mock_post):
        client = _bailian_client()
        sse = _sse_response([{"choices": [{"delta": {"content": "ok"},
                                           "finish_reason": "stop"}]}])
        side, bodies = _recording_side_effect([sse])
        mock_post.side_effect = side

        chunks = list(client.chat_stream([{"role": "user", "content": "hi"}], tools=TOOLS))

        self.assertTrue(bodies[0]["enable_search"])
        self.assertTrue(any(c.text == "ok" for c in chunks))

    @patch("core.llm_client.requests.post")
    def test_stream_rejected_then_disabled(self, mock_post):
        client = _bailian_client()
        sse = _sse_response([{"choices": [{"delta": {"content": "ok"},
                                           "finish_reason": "stop"}]}])
        side, bodies = _recording_side_effect([
            _http_error(400, "enable_search is not supported"),
            sse,
        ])
        mock_post.side_effect = side

        chunks = list(client.chat_stream([{"role": "user", "content": "hi"}], tools=TOOLS))

        self.assertEqual(len(bodies), 2)
        self.assertTrue(bodies[0]["enable_search"])
        self.assertNotIn("enable_search", bodies[1])
        self.assertTrue(client._server_search_rejected)
        self.assertTrue(any(c.text == "ok" for c in chunks))


class _StubLLM:
    """agent 层测试桩：模拟 OpenAICompatibleClient 的联网开关行为。

    server_capable=False 模拟非百炼端点（Ollama/DeepSeek）：开关开着但
    服务端搜索不可用，server_web_search_active 必须为 False——与真实客户端
    的 is_bailian 检查语义一致。
    """

    provider = "openai_compatible"
    supports_vision = False

    def __init__(self, server_capable):
        self.is_bailian = server_capable
        self.server_tools = {"web_search": True}
        self._server_search_rejected = False

    @property
    def server_web_search_active(self):
        return (bool(self.server_tools.get("web_search"))
                and self.is_bailian
                and not self._server_search_rejected)

    def chat(self, messages, tools=None):
        return ChatResponse(text="ok")

    def chat_stream(self, messages, tools=None):
        yield ChatResponse(text="ok")


class TestAgentLayer(unittest.TestCase):
    """agent 层：总开关三态的工具隐藏 + system 提示注入 + 运行时切换"""

    @staticmethod
    def _agent(active, web_search_enabled=True):
        import tempfile
        return Agent(
            config={"llm": {"provider": "deterministic"},
                    "agent": {"max_steps": 5, "confirm_high_risk": False},
                    "web_search": {"enabled": web_search_enabled},
                    "memory": {"storage_dir": tempfile.mkdtemp(prefix="miniyu_test_")}},
            llm_client=_StubLLM(active),
        )

    @staticmethod
    def _names(tools):
        return [t["function"]["name"] for t in tools]

    def test_visible_tools_hide_and_restore(self):
        full = self._agent(False)._agent_openai_tools()
        self.assertIn("browser_search", self._names(full))

        # 开 + 服务端搜索生效：只藏 browser_search
        agent_on = self._agent(True)
        visible = agent_on._visible_tools(full)
        self.assertNotIn("browser_search", self._names(visible))
        self.assertIn("browser_extract", self._names(visible))  # 深读配套保留
        self.assertEqual(len(full) - len(visible), 1)

        # 开 + 服务端不生效（非百炼端点）：全部可见
        agent_off = self._agent(False)
        self.assertIn("browser_search", self._names(agent_off._visible_tools(full)))

        # 全集不受影响（统计/测试引用仍可见）
        self.assertIn("browser_search", self._names(agent_on._agent_openai_tools()))

    def test_visible_tools_offline_hides_both(self):
        """总开关关：browser_search + browser_extract 都隐藏（完全离线）"""
        full = self._agent(True)._agent_openai_tools()
        agent = self._agent(True, web_search_enabled=False)
        visible = agent._visible_tools(full)
        names = self._names(visible)
        self.assertNotIn("browser_search", names)
        self.assertNotIn("browser_extract", names)
        self.assertEqual(len(full) - len(visible), 2)

    def test_request_messages_hint(self):
        # 开 + 服务端生效 → 服务端提示
        agent_on = self._agent(True)
        sysmsg = agent_on._request_messages()[0]
        self.assertEqual(sysmsg["role"], "system")
        self.assertIn("服务端联网搜索", sysmsg["content"])
        self.assertIn("覆盖守则第 9 条", sysmsg["content"])
        self.assertNotIn("离线模式", sysmsg["content"])

        # 开 + 服务端不生效 → 无任何联网提示（本地搜索工具可见，守则第 9 条照常）
        agent_local = self._agent(False)
        self.assertNotIn("服务端联网搜索", agent_local._request_messages()[0]["content"])
        self.assertNotIn("离线模式", agent_local._request_messages()[0]["content"])

        # 关 → 离线提示
        agent_off = self._agent(True, web_search_enabled=False)
        offmsg = agent_off._request_messages()[0]["content"]
        self.assertIn("离线模式", offmsg)
        self.assertIn("覆盖守则第 9 条", offmsg)
        self.assertNotIn("服务端联网搜索", offmsg)

    def test_set_web_search_enabled_runtime(self):
        """运行时切换：server_tools 联动 + server_web_search_active 跟随 + 即时生效"""
        agent = self._agent(True)
        self.assertTrue(agent.llm.server_web_search_active)

        agent.set_web_search_enabled(False)
        self.assertFalse(agent.web_search_enabled)
        self.assertFalse(agent.llm.server_web_search_active)
        full = agent._agent_openai_tools()
        self.assertNotIn("browser_search", self._names(agent._visible_tools(full)))
        self.assertNotIn("browser_extract", self._names(agent._visible_tools(full)))
        self.assertIn("离线模式", agent._request_messages()[0]["content"])

        agent.set_web_search_enabled(True)
        self.assertTrue(agent.llm.server_web_search_active)
        self.assertIn("browser_extract", self._names(agent._visible_tools(full)))
        self.assertNotIn("离线模式", agent._request_messages()[0]["content"])


class TestFailoverDelegation(unittest.TestCase):
    """FailoverClient：跟随主 LLM，降级/本地模式下不可用"""

    def test_delegates_to_primary(self):
        fc = FailoverClient(
            primary=_bailian_client(),
            fallback=OpenAICompatibleClient(base_url=OLLAMA_URL, api_key="k", model="m"),
        )
        self.assertTrue(fc.server_web_search_active)

    def test_degraded_returns_false(self):
        fc = FailoverClient(primary=_bailian_client(), fallback=None)
        fc._degraded = True
        self.assertFalse(fc.server_web_search_active)

    def test_force_local_returns_false(self):
        fc = FailoverClient(primary=_bailian_client(), fallback=None)
        fc._force_local = True
        self.assertFalse(fc.server_web_search_active)


if __name__ == "__main__":
    unittest.main()
