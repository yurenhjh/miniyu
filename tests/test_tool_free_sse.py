"""
test_tool_free_sse.py：纯对话模式（tool_free）+ 思考流（reasoning/SSE）单测

背景（实测）：本机无 GPU，63 个工具 schema ≈ 7.7K token，纯 CPU 的 prompt eval
要 33~150s+，本地小模型只适合日常对话。tool_free 让本地端点不传工具、agent
换轻量 system prompt；reasoning_content/reasoning 单独走 "reasoning" 通道供
前端渲染 DeepSeek 式思考过程；Web 端 /task/<id>/events 用 SSE 事件流转发
全过程（思考/正文/工具/确认/终态）。
全部离线：HTTP 层用桩对象，Agent/Web 用假客户端，不碰真实 API 与网络。
"""

import json
import queue
import sys
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "examples") not in sys.path:
    sys.path.insert(0, str(_ROOT / "examples"))

from core.agent import Agent
from core.llm_client import (
    ChatResponse,
    FailoverClient,
    OpenAICompatibleClient,
    create_llm_client,
)
import miniyu_web


# ============================================================
# HTTP 桩
# ============================================================

class _FakeJsonResp:
    """非流式响应桩：raise_for_status + json()"""

    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeStreamResp:
    """流式响应桩：iter_content 吐一段完整 SSE 文本"""

    def __init__(self, sse_text):
        self._sse = sse_text
        self.status_code = 200

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=None, decode_unicode=True):
        yield self._sse


def _sse(*datas):
    """拼 OpenAI 风格 SSE 文本（最后补 [DONE]）"""
    return "".join(
        f"data: {json.dumps(d, ensure_ascii=False)}\n\n" for d in datas
    ) + "data: [DONE]\n\n"


# ============================================================
# OpenAICompatibleClient：tool_free + reasoning 捕获
# ============================================================

class TestToolFreeClient:

    @staticmethod
    def _client(tool_free):
        return OpenAICompatibleClient(
            base_url="http://localhost:11434/v1", api_key="",
            model="local:7b", tool_free=tool_free,
        )

    def test_chat_drops_tools(self, monkeypatch):
        """tool_free：请求体彻底不带 tools（省掉 7.7K token 的 prompt eval）"""
        captured = {}
        payload = {"choices": [{"message": {"role": "assistant", "content": "你好"},
                                "finish_reason": "stop"}], "usage": None}

        def fake_post(url, headers=None, json=None, timeout=None, **kw):
            captured["body"] = json
            return _FakeJsonResp(payload)

        monkeypatch.setattr("core.llm_client.requests.post", fake_post)
        tools = [{"type": "function", "function": {"name": "disk_usage", "parameters": {}}}]
        resp = self._client(tool_free=True).chat(
            [{"role": "user", "content": "你好"}], tools=tools)
        assert "tools" not in captured["body"]
        assert resp.text == "你好"

    def test_chat_keeps_tools_when_not_tool_free(self, monkeypatch):
        """非 tool_free：工具照常下发（在线模型功能不受影响）"""
        captured = {}
        payload = {"choices": [{"message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop"}], "usage": None}

        def fake_post(url, headers=None, json=None, timeout=None, **kw):
            captured["body"] = json
            return _FakeJsonResp(payload)

        monkeypatch.setattr("core.llm_client.requests.post", fake_post)
        tools = [{"type": "function", "function": {"name": "disk_usage", "parameters": {}}}]
        self._client(tool_free=False).chat([{"role": "user", "content": "q"}], tools=tools)
        assert captured["body"]["tools"] == tools

    def test_chat_timeout_hint_for_local_model(self, monkeypatch):
        """tool_free 超时文案引导用户用流式 Web 界面 / 调大 timeout"""
        import requests as rq

        def fake_post(url, headers=None, json=None, timeout=None, **kw):
            raise rq.exceptions.Timeout()

        monkeypatch.setattr("core.llm_client.requests.post", fake_post)
        resp = self._client(tool_free=True).chat([{"role": "user", "content": "写长文"}])
        assert "本地模型" in resp.text
        assert "流式" in resp.text

    def test_stream_drops_tools(self, monkeypatch):
        """流式 tool_free：请求体不带 tools"""
        captured = {}
        sse_text = _sse(
            {"choices": [{"delta": {"content": "嗨"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        )

        def fake_post(url, headers=None, json=None, timeout=None, stream=False, **kw):
            captured["body"] = json
            return _FakeStreamResp(sse_text)

        monkeypatch.setattr("core.llm_client.requests.post", fake_post)
        chunks = list(self._client(tool_free=True).chat_stream(
            [{"role": "user", "content": "q"}],
            tools=[{"type": "function", "function": {"name": "x"}}]))
        assert "tools" not in captured["body"]
        assert [c.finish_reason for c in chunks] == ["streaming"]
        assert chunks[0].text == "嗨"

    def test_stream_reasoning_two_field_names(self, monkeypatch):
        """思考 token：百炼/DeepSeek 的 reasoning_content 与 Ollama 的 reasoning
        都单独走 "reasoning" 通道（前端据此渲染 DeepSeek 式思考过程）"""
        sse_text = _sse(
            {"choices": [{"delta": {"reasoning_content": "先想一下，"}}]},
            {"choices": [{"delta": {"reasoning": "再想一下。"}}]},
            {"choices": [{"delta": {"content": "答案是42"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}],
             "usage": {"total_tokens": 9}},
        )
        monkeypatch.setattr(
            "core.llm_client.requests.post", lambda *a, **k: _FakeStreamResp(sse_text))
        chunks = list(self._client(tool_free=True).chat_stream(
            [{"role": "user", "content": "q"}]))
        assert [c.finish_reason for c in chunks] == ["reasoning", "reasoning", "streaming"]
        assert chunks[0].reasoning == "先想一下，"
        assert chunks[1].reasoning == "再想一下。"
        assert chunks[2].text == "答案是42"

    def test_chat_captures_reasoning_nonstream(self, monkeypatch):
        """非流式：message 里的 reasoning_content 也进 resp.reasoning"""
        payload = {"choices": [{"message": {"role": "assistant", "content": "答案",
                                            "reasoning_content": "思考过程"},
                                "finish_reason": "stop"}], "usage": None}
        monkeypatch.setattr(
            "core.llm_client.requests.post", lambda *a, **k: _FakeJsonResp(payload))
        resp = self._client(tool_free=True).chat([{"role": "user", "content": "q"}])
        assert resp.text == "答案"
        assert resp.reasoning == "思考过程"


# ============================================================
# 工厂：本地端点默认 tool_free，可显式覆盖
# ============================================================

class TestToolFreeFactory:

    def test_local_url_defaults_tool_free(self):
        c = create_llm_client({"llm": {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "api_key": "", "model": "qwen:7b",
        }})
        assert isinstance(c, OpenAICompatibleClient)
        assert c.tool_free is True

    def test_127_url_defaults_tool_free(self):
        c = create_llm_client({"llm": {
            "provider": "openai_compatible",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "", "model": "qwen:4b",
        }})
        assert c.tool_free is True

    def test_remote_url_not_tool_free(self):
        c = create_llm_client({"llm": {
            "provider": "openai_compatible",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "k", "model": "deepseek-chat",
        }})
        assert isinstance(c, OpenAICompatibleClient)
        assert c.tool_free is False

    def test_explicit_false_overrides_local_default(self):
        c = create_llm_client({"llm": {
            "provider": "openai_compatible",
            "base_url": "http://localhost:11434/v1",
            "api_key": "", "model": "m", "tool_free": False,
        }})
        assert c.tool_free is False

    def test_fallback_local_defaults_tool_free(self):
        """主 API 在线 + 本地 fallback：fallback 默认纯对话，主 LLM 不受影响"""
        fc = create_llm_client({"llm": {
            "provider": "openai_compatible",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "k", "model": "deepseek-chat",
            "fallback": {
                "provider": "openai_compatible",
                "base_url": "http://127.0.0.1:11434/v1",
                "api_key": "", "model": "qwen:4b",
            },
        }})
        assert isinstance(fc, FailoverClient)
        assert fc.primary.tool_free is False
        assert fc.fallback.tool_free is True
        assert fc.tool_free_active is False   # 未降级：主 LLM 正常带工具


# ============================================================
# FailoverClient：tool_free_active 两个触发条件
# ============================================================

class _StubLLM:
    provider = "stub"
    supports_vision = False
    model = "stub-model"

    def __init__(self, text="ok", fail=False, tool_free=False):
        self.text = text
        self.fail = fail
        self.tool_free = tool_free
        self.chat_calls = 0
        self.stream_calls = 0

    def chat(self, messages, tools=None):
        self.chat_calls += 1
        if self.fail:
            raise ConnectionError("boom")
        return ChatResponse(text=self.text)

    def chat_stream(self, messages, tools=None):
        self.stream_calls += 1
        if self.fail:
            raise ConnectionError("boom")
        yield ChatResponse(text=self.text, finish_reason="stop")


class TestFailoverToolFree:

    def test_primary_tool_free_direct(self):
        """触发①：主 LLM 本身声明 tool_free（离线增强模式）"""
        fc = FailoverClient(primary=_StubLLM(tool_free=True))
        assert fc.tool_free_active is True

    def test_not_active_when_primary_ok(self):
        """主 LLM 正常时即使 fallback 是 tool_free 也不激活"""
        fc = FailoverClient(primary=_StubLLM(), fallback=_StubLLM(tool_free=True))
        assert fc.tool_free_active is False

    def test_active_after_degrade(self):
        """触发②：主 LLM 挂掉降级到 tool_free 的本地模型"""
        primary = _StubLLM(fail=True)
        fallback = _StubLLM(tool_free=True, text="local")
        fc = FailoverClient(primary=primary, fallback=fallback)
        resp = fc.chat([{"role": "user", "content": "hi"}])
        assert resp.text == "local"
        assert fc.status["degraded"]
        assert fc.tool_free_active is True

    def test_force_local_stream_goes_fallback_only(self):
        """手动切本地模式：流式直接走 fallback，不再打主 API"""
        primary = _StubLLM(text="cloud")
        fallback = _StubLLM(tool_free=True, text="local")
        fc = FailoverClient(primary=primary, fallback=fallback)
        assert fc.force_local_mode(True)
        assert fc.tool_free_active is True
        chunks = list(fc.chat_stream([{"role": "user", "content": "hi"}]))
        assert chunks[0].text == "local"
        assert fallback.stream_calls == 1
        assert primary.stream_calls == 0


# ============================================================
# Agent：纯对话模式分支
# ============================================================

class _RecordingChatLLM:
    """记录 chat_stream 收到的 messages/tools，吐 思考→正文→stop 序列"""
    provider = "openai_compatible"
    supports_vision = False
    tool_free = True

    def __init__(self):
        self.calls = []

    def chat_stream(self, messages, tools=None):
        self.calls.append((messages, tools))
        yield ChatResponse(reasoning="构思开头", finish_reason="reasoning")
        yield ChatResponse(text="从前有座山", finish_reason="streaming")
        yield ChatResponse(text="从前有座山", finish_reason="stop")

    def chat(self, messages, tools=None):
        self.calls.append((messages, tools))
        return ChatResponse(text="从前有座山")


class TestAgentChatMode:

    def test_chat_mode_light_prompt_no_tools_reasoning_passthrough(self, tmp_path):
        """纯对话模式：不传工具 schema、system 换轻量提示、思考 chunk 透传"""
        llm = _RecordingChatLLM()
        agent = Agent(
            config={"llm": {"provider": "openai_compatible"},
                    "agent": {"max_steps": 5, "confirm_high_risk": False},
                    "memory": {"storage_dir": str(tmp_path)}},
            llm_client=llm,
            confirm_handler=None,
        )
        chunks = list(agent.run_stream("写个故事"))
        assert llm.calls, "应至少调用一次 chat_stream"
        msgs, tools = llm.calls[0]
        assert tools is None                        # 不带 63 个工具 schema
        assert msgs[0]["role"] == "system"
        assert "纯对话模式" in msgs[0]["content"]    # 轻量提示（非工具守则）
        assert any(c.finish_reason == "reasoning" for c in chunks)
        assert any(c.finish_reason == "stop" for c in chunks)
        assert any(c.finish_reason == "streaming" and c.text == "从前有座山"
                   for c in chunks)

    def test_chat_mode_not_triggered_when_tools_supported(self, tmp_path):
        """在线模型（未声明 tool_free）：照常传工具"""
        llm = _RecordingChatLLM()
        llm.tool_free = False
        agent = Agent(
            config={"llm": {"provider": "openai_compatible"},
                    "agent": {"max_steps": 5, "confirm_high_risk": False},
                    "memory": {"storage_dir": str(tmp_path)}},
            llm_client=llm,
            confirm_handler=None,
        )
        list(agent.run_stream("整理桌面"))
        _, tools = llm.calls[0]
        assert tools, "在线模式应照常下发工具"


# ============================================================
# Web SSE 管线：/chat → 事件队列 → /task/<id>/events → 终态清理
# ============================================================

class _FakeAgent:
    """假 Agent：run_stream 吐预设 chunk 序列或抛异常"""

    def __init__(self, chunks=None, error=None):
        self._chunks = chunks or []
        self._error = error

    def run_stream(self, message, stop_check=None):
        if self._error:
            raise self._error
        yield from self._chunks


class TestWebSSEPipeline:

    def teardown_method(self):
        with miniyu_web._tasks_lock:
            miniyu_web._tasks.clear()

    @staticmethod
    def _post_chat(monkeypatch, agent):
        monkeypatch.setattr(miniyu_web, "_ensure_agent", lambda: agent)
        client = miniyu_web.app.test_client()
        resp = client.post("/chat", json={"message": "写个故事"})
        assert resp.status_code == 200
        return client, resp.get_json()["task_id"]

    @staticmethod
    def _drain_events(client, task_id):
        resp = client.get(f"/task/{task_id}/events")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        body = resp.get_data(as_text=True)
        return [json.loads(line[6:]) for line in body.splitlines()
                if line.startswith("data: ")]

    def test_full_event_sequence(self, monkeypatch):
        """思考→正文→工具→done 事件按序抵达，done 后任务清理"""
        agent = _FakeAgent(chunks=[
            ChatResponse(reasoning="构思开头", finish_reason="reasoning"),
            ChatResponse(text="从前有", finish_reason="streaming"),
            ChatResponse(text="座山", finish_reason="streaming"),
            ChatResponse(tool_calls=[{"name": "disk_usage",
                                      "arguments": {"path": "C:"}}],
                         finish_reason="tool_calls"),
            ChatResponse(text="从前有座山", finish_reason="stop"),
        ])
        client, task_id = self._post_chat(monkeypatch, agent)
        events = self._drain_events(client, task_id)
        assert [e["type"] for e in events] == \
            ["reasoning", "token", "token", "tool_call", "done"]
        assert events[0]["delta"] == "构思开头"
        assert events[1]["delta"] == "从前有"
        assert events[3]["tool"] == "disk_usage"
        assert events[3]["arguments"] == {"path": "C:"}
        assert events[4]["result"] == "从前有座山"
        # done 消费后任务已清理
        resp = client.get(f"/task/{task_id}")
        assert resp.get_json()["result"] == "任务不存在"

    def test_error_event(self, monkeypatch):
        """agent 异常 → error 事件带原因，同样终止 SSE 流"""
        agent = _FakeAgent(error=RuntimeError("boom"))
        client, task_id = self._post_chat(monkeypatch, agent)
        events = self._drain_events(client, task_id)
        assert events[-1]["type"] == "error"
        assert "boom" in events[-1]["result"]

    def test_confirm_handler_without_task_denies(self):
        """无任务上下文（异常路径）→ 直接拒绝，不挂死"""
        with pytest.raises(miniyu_web.ConfirmationDenied):
            miniyu_web._web_confirm_handler(
                {"name": "x", "description": "", "params": {}, "risk": "high"})

    def test_confirm_flow_allow_and_deny(self, monkeypatch):
        """确认门：confirm 事件入 SSE 队列；放行返回 True，拒绝抛 ConfirmationDenied"""
        client = miniyu_web.app.test_client()

        def run_case(allow):
            task_id = f"conf_{allow}"
            q = queue.Queue()
            with miniyu_web._tasks_lock:
                miniyu_web._tasks[task_id] = {"status": "pending", "result": None,
                                              "events": q}
            outcome = {}

            def agent_side():
                # _web_confirm_handler 依赖 thread-local 任务 ID，必须在执行线程内设置
                miniyu_web._set_current_task_id(task_id)
                try:
                    outcome["allowed"] = miniyu_web._web_confirm_handler({
                        "name": "delete_file", "description": "永久删除文件",
                        "params": {"path": "C:/x.txt"}, "risk": "high",
                    })
                except Exception as e:
                    outcome["denied"] = type(e).__name__

            t = threading.Thread(target=agent_side, daemon=True)
            t.start()
            item = q.get(timeout=5)          # confirm 事件应进入 SSE 队列
            assert item["type"] == "confirm"
            assert item["tool"] == "delete_file"
            assert item["risk"] == "high"
            # 确认等待期间任务状态为 confirm，且 SSE 事件队列被保留（未被覆盖丢失）
            with miniyu_web._tasks_lock:
                assert miniyu_web._tasks[task_id]["status"] == "confirm"
                assert miniyu_web._tasks[task_id]["events"] is q
            resp = client.post(f"/confirm/{task_id}", json={"allow": allow})
            assert resp.get_json()["success"] is True
            t.join(timeout=5)
            assert not t.is_alive()
            return outcome

        assert run_case(True).get("allowed") is True
        assert run_case(False).get("denied") == "ConfirmationDenied"
