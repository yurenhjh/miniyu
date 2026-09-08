"""
test_tool_protocol.py
工具协议自适应单元测试
覆盖：FailoverClient tools 透传、400 自适应（tools 参数被拒 / 历史 tool 消息被拒）、
文本协议模式（<tool_call> 标记解析、历史转换、system prompt 注入）、标志重置
"""

import json
import unittest
from unittest.mock import MagicMock, patch

import requests

from core.llm_client import (
    ChatResponse,
    DeterministicBrain,
    FailoverClient,
    OpenAICompatibleClient,
)


def _make_client(model="test-model"):
    return OpenAICompatibleClient(
        base_url="http://localhost:11434/v1",
        api_key="test-key",
        model=model,
        timeout=5,
    )


def _http_error(status, message):
    """构造带 JSON 错误体的 HTTPError"""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {"error": {"message": message}}
    err = requests.exceptions.HTTPError(f"{status} error")
    err.response = resp
    return err


def _mock_post_side_effect(responses):
    """生成 requests.post 打桩：按顺序抛错或返回响应"""
    calls = {"n": 0}

    def side_effect(url, headers=None, json=None, timeout=None, stream=False):
        i = calls["n"]
        calls["n"] += 1
        action = responses[min(i, len(responses) - 1)]
        if isinstance(action, Exception):
            raise action
        if hasattr(action, "iter_content"):
            return action  # 预构造的响应 mock（如 SSE 流），原样返回
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status.return_value = None
        mock.json.return_value = action
        return mock

    return side_effect, calls


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


def _ok_response(content="", tool_calls=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = [{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": tool_calls[0],
                "arguments": json.dumps(tool_calls[1]),
            },
        }]
    return {
        "choices": [{"message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"total_tokens": 10},
    }


class TestParsingHelpers(unittest.TestCase):
    """纯函数解析测试（不打桩）"""

    def setUp(self):
        self.client = _make_client()

    def test_parse_tool_call_tags_normal(self):
        text = '我来帮你整理。\n<tool_call>{"name": "sort_directory", "arguments": {"path": "~/Desktop"}}</tool_call>'
        calls = self.client._parse_tool_call_tags(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "sort_directory")
        self.assertEqual(calls[0]["arguments"], {"path": "~/Desktop"})

    def test_parse_tool_call_tags_multiple(self):
        text = ('<tool_call>{"name": "a", "arguments": {}}</tool_call>'
                '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>')
        calls = self.client._parse_tool_call_tags(text)
        self.assertEqual(len(calls), 2)
        self.assertEqual([c["name"] for c in calls], ["a", "b"])

    def test_parse_tool_call_tags_args_as_string(self):
        text = '<tool_call>{"name": "sort_directory", "arguments": "{\\"path\\": \\"/tmp\\"}"}</tool_call>'
        calls = self.client._parse_tool_call_tags(text)
        self.assertEqual(calls[0]["arguments"], {"path": "/tmp"})

    def test_parse_tool_call_tags_invalid(self):
        self.assertEqual(self.client._parse_tool_call_tags("没有调用"), [])
        self.assertEqual(
            self.client._parse_tool_call_tags('<tool_call>{bad json}</tool_call>'), []
        )

    def test_build_tools_prompt_contains_all(self):
        prompt = self.client._build_tools_prompt(TOOLS)
        self.assertIn("sort_directory", prompt)
        self.assertIn("整理目录", prompt)
        self.assertIn("<tool_call>", prompt)

    def test_convert_history_to_text(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "整理桌面"},
            {  # OpenAI 原生 assistant tool_calls
                "role": "assistant", "content": None,
                "tool_calls": [{
                    "id": "c1", "type": "function",
                    "function": {"name": "sort_directory", "arguments": '{"path": "~/Desktop"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
        ]
        out = self.client._convert_history_to_text(messages)
        self.assertEqual(out[0], {"role": "system", "content": "sys"})
        self.assertEqual(out[1], {"role": "user", "content": "整理桌面"})
        self.assertEqual(out[2]["role"], "assistant")
        self.assertIn('<tool_call>{"name": "sort_directory"', out[2]["content"])
        self.assertEqual(out[3]["role"], "user")
        self.assertIn("<tool_result>done</tool_result>", out[3]["content"])

    def test_convert_history_tool_result_dict(self):
        messages = [{"role": "tool", "tool_call_id": "c1", "content": {"success": True}}]
        out = self.client._convert_history_to_text(messages)
        self.assertIn('"success"', out[0]["content"])  # dict 自动序列化

    def test_sanitize_tool_messages(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "res"},
        ]
        out = self.client._sanitize_tool_messages(messages)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[1], {"role": "assistant", "content": "[工具调用已执行]"})

    def test_has_tool_messages(self):
        self.assertFalse(self.client._has_tool_messages([{"role": "user", "content": "x"}]))
        self.assertTrue(self.client._has_tool_messages(
            [{"role": "assistant", "tool_calls": [{"function": {"name": "x"}}]}]))
        self.assertTrue(self.client._has_tool_messages([{"role": "tool", "content": "x"}]))


class TestChatAdaptiveFallback(unittest.TestCase):
    """chat() 400 自适应测试（mock requests.post）"""

    def setUp(self):
        self.client = _make_client()

    @patch("core.llm_client.requests.post")
    def test_tools_param_rejected_then_text_protocol(self, mock_post):
        """模型不支持 tools 参数 → 置标志 + 文本协议重试成功"""
        text_reply = _ok_response(
            content='<tool_call>{"name": "sort_directory", "arguments": {"path": "~/Desktop"}}</tool_call>'
        )
        side_effect, calls = _mock_post_side_effect([
            _http_error(400, "The tool call is not supported."),
            text_reply,
        ])
        mock_post.side_effect = side_effect

        resp = self.client.chat([{"role": "user", "content": "整理桌面"}], tools=TOOLS)

        self.assertEqual(calls["n"], 2)  # 第一次被拒，第二次文本协议
        self.assertTrue(self.client._tools_param_rejected)
        self.assertEqual(resp.finish_reason, "tool_calls")
        self.assertEqual(resp.tool_calls[0]["name"], "sort_directory")
        # 第二次请求不带 tools 参数，且 system 注入了工具说明
        second_body = mock_post.call_args_list[1].kwargs["json"]
        self.assertNotIn("tools", second_body)
        self.assertIn("# 可用工具", second_body["messages"][0]["content"])
        self.assertIn("sort_directory", second_body["messages"][0]["content"])

    @patch("core.llm_client.requests.post")
    def test_tools_param_rejected_persistent(self, _mock_outer):
        """置标志后，后续请求直接走文本协议（不再先发 tools）"""
        with patch("core.llm_client.requests.post") as mock_post:
            side_effect, calls = _mock_post_side_effect([
                _http_error(400, "The tool call is not supported."),
                _ok_response(content="ok"),
            ])
            mock_post.side_effect = side_effect
            self.client.chat([{"role": "user", "content": "x"}], tools=TOOLS)

        # 第二次调用：直接文本协议（1 次请求即成功）
        with patch("core.llm_client.requests.post") as mock_post:
            side_effect, calls = _mock_post_side_effect([_ok_response(content="ok")])
            mock_post.side_effect = side_effect
            self.client.chat([{"role": "user", "content": "y"}], tools=TOOLS)
            self.assertEqual(calls["n"], 1)
            body = mock_post.call_args_list[0].kwargs["json"]
            self.assertNotIn("tools", body)
            self.assertIn("# 可用工具", body["messages"][0]["content"])

    @patch("core.llm_client.requests.post")
    def test_tool_history_rejected_then_sanitize_retry(self, mock_post):
        """历史 tool 消息被拒 → sanitize 后重试成功，且历史里 tool 消息被清理"""
        ok = _ok_response(content="已整理完成")
        side_effect, calls = _mock_post_side_effect([
            _http_error(400, 'messages with role "tool" must be a response to a preceding message with "tool_calls"'),
            ok,
        ])
        mock_post.side_effect = side_effect

        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "整理"},
            {"role": "assistant", "content": None,
             "tool_calls": [{"id": "c1", "type": "function",
                             "function": {"name": "sort_directory", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "done"},
            {"role": "user", "content": "继续"},
        ]
        resp = self.client.chat(messages, tools=TOOLS)

        self.assertEqual(calls["n"], 2)
        self.assertTrue(self.client._tool_history_rejected)
        self.assertEqual(resp.text, "已整理完成")
        # 重试请求的历史里没有 tool 消息
        retry_body = mock_post.call_args_list[1].kwargs["json"]
        roles = [m["role"] for m in retry_body["messages"]]
        self.assertNotIn("tool", roles)

    @patch("core.llm_client.requests.post")
    def test_history_rejected_flag_preprocesses_next_request(self, mock_post):
        """置标志后，后续含 tool 历史的请求自动预清理（1 次即成功）"""
        with patch("core.llm_client.requests.post") as mp:
            side_effect, calls = _mock_post_side_effect([
                _http_error(400, 'role "tool" invalid'),
                _ok_response(content="ok"),
            ])
            mp.side_effect = side_effect
            msgs = [
                {"role": "assistant", "tool_calls": [{"function": {"name": "x", "arguments": "{}"}}]},
                {"role": "tool", "content": "r"},
            ]
            self.client.chat(msgs, tools=TOOLS)

        # 新请求带 tool 历史 → 应被预清理，直接 1 次成功
        side_effect, calls = _mock_post_side_effect([_ok_response(content="ok2")])
        mock_post.side_effect = side_effect
        msgs2 = [
            {"role": "user", "content": "q"},
            {"role": "tool", "content": "r"},
        ]
        resp = self.client.chat(msgs2, tools=TOOLS)
        self.assertEqual(calls["n"], 1)
        body = mock_post.call_args_list[0].kwargs["json"]
        roles = [m["role"] for m in body["messages"]]
        self.assertNotIn("tool", roles)

    @patch("core.llm_client.requests.post")
    def test_unrelated_400_not_intercepted(self, mock_post):
        """非工具相关 400 不触发自适应，走常规错误提示"""
        side_effect, calls = _mock_post_side_effect([
            _http_error(400, "Invalid model name"),
        ])
        mock_post.side_effect = side_effect
        resp = self.client.chat([{"role": "user", "content": "x"}], tools=TOOLS)
        self.assertEqual(calls["n"], 1)
        self.assertFalse(self.client._tools_param_rejected)
        self.assertFalse(self.client._tool_history_rejected)
        self.assertIn("400", resp.text)

    @patch("core.llm_client.requests.post")
    def test_reset_runtime_flags(self, mock_post):
        """reset 后重新探测（第一次又发 tools 参数）"""
        self.client._tools_param_rejected = True
        self.client.reset_runtime_flags()
        self.assertFalse(self.client._tools_param_rejected)
        self.assertFalse(self.client._tool_history_rejected)


class TestChatStreamAdaptive(unittest.TestCase):
    """chat_stream() 400 自适应测试"""

    def setUp(self):
        self.client = _make_client()

    @staticmethod
    def _sse_response(dicts):
        """构造 SSE 流响应 mock"""
        mock = MagicMock()
        mock.status_code = 200
        mock.raise_for_status.return_value = None
        chunks = []
        for d in dicts:
            chunks.append(f"data: {json.dumps(d)}\n".encode())
        chunks.append(b"data: [DONE]\n")
        mock.iter_content.return_value = chunks
        return mock

    @patch("core.llm_client.requests.post")
    def test_stream_tools_param_rejected(self, mock_post):
        """流式：模型不支持 tools → 文本协议流式重试"""
        sse = self._sse_response([{
            "choices": [{"delta": {"content":
                '<tool_call>{"name": "sort_directory", "arguments": {"path": "/tmp"}}</tool_call>'}}]
        }])
        side_effect, calls = _mock_post_side_effect([
            _http_error(400, "The tool call is not supported."),
            sse,
        ])
        mock_post.side_effect = side_effect

        chunks = list(self.client.chat_stream(
            [{"role": "user", "content": "整理"}], tools=TOOLS))

        self.assertEqual(calls["n"], 2)
        self.assertTrue(self.client._tools_param_rejected)
        tc_chunks = [c for c in chunks if c.finish_reason == "tool_calls"]
        self.assertEqual(len(tc_chunks), 1)
        self.assertEqual(tc_chunks[0].tool_calls[0]["name"], "sort_directory")
        # 第二次请求不带 tools
        second_body = mock_post.call_args_list[1].kwargs["json"]
        self.assertNotIn("tools", second_body)

    @patch("core.llm_client.requests.post")
    def test_stream_history_rejected(self, mock_post):
        """流式：历史 tool 消息被拒 → sanitize 重试"""
        sse = self._sse_response([{
            "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]
        }])
        side_effect, calls = _mock_post_side_effect([
            _http_error(400, 'messages with role "tool" must follow'),
            sse,
        ])
        mock_post.side_effect = side_effect

        msgs = [
            {"role": "user", "content": "q"},
            {"role": "tool", "content": "r"},
        ]
        chunks = list(self.client.chat_stream(msgs, tools=TOOLS))
        self.assertEqual(calls["n"], 2)
        self.assertTrue(self.client._tool_history_rejected)
        self.assertTrue(any(c.text == "ok" for c in chunks))


class TestFailoverToolsPassthrough(unittest.TestCase):
    """FailoverClient 工具透传 + 切换重置"""

    def setUp(self):
        self.primary = _make_client("primary-model")
        self.fallback = _make_client("fallback-model")
        self.fc = FailoverClient(primary=self.primary, fallback=self.fallback)

    def test_chat_passes_tools_to_primary(self):
        """FailoverClient.chat 应把 tools 透传给 primary"""
        with patch.object(self.primary, "chat", wraps=self.primary.chat) as spy:
            with patch("core.llm_client.requests.post") as mock_post:
                side_effect, _ = _mock_post_side_effect([_ok_response(content="ok")])
                mock_post.side_effect = side_effect
                self.fc.chat([{"role": "user", "content": "x"}], tools=TOOLS)
        spy.assert_called_once()
        # 确认 tools 到达了底层请求
        body = mock_post.call_args_list[0].kwargs["json"]
        self.assertEqual(body.get("tools"), TOOLS)

    def test_switch_models_reset_flags(self):
        """切换主/备模型后 reset_runtime_flags 被调用"""
        self.primary._tools_param_rejected = True
        self.fallback._tool_history_rejected = True
        self.assertTrue(self.fc.switch_primary_model("new-primary"))
        self.assertTrue(self.fc.switch_fallback_model("new-fallback"))
        self.assertEqual(self.primary.model, "new-primary")
        self.assertEqual(self.fallback.model, "new-fallback")
        self.assertFalse(self.primary._tools_param_rejected)
        self.assertFalse(self.fallback._tool_history_rejected)


if __name__ == "__main__":
    unittest.main()
