"""
test_stream_stop_encoding.py：流式 UTF-8 解码修复 + "停止生成"打断功能单测

背景（实测 2026-09-07）：Ollama 流式响应头 Content-Type: text/event-stream 不带
charset，requests.iter_content(decode_unicode=True) 会按推断的 ISO-8859-1 解码，
UTF-8 中文被逐字节解成 'ä½ å¥½' 式乱码（Web UI 本地模型回复全部 mojibake）。
修复：按字节读流 + codecs 增量 UTF-8 解码器（顺带容忍网络块切在多字节字符中间）。

打断功能：Agent.run_stream(stop_check=...) 在流式 chunk 间隙检查回调，True 时
保留已流出文本立即终止；Web 端 /task/<id>/stop 置位 stop_event，前端"⏹ 停止"
按钮调用（DeepSeek 式打断，防本地小模型死循环输出刷屏）。
全部离线：HTTP 层用字节桩，Agent/Web 用假客户端，不碰真实 API 与网络。
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "examples") not in sys.path:
    sys.path.insert(0, str(_ROOT / "examples"))

from core.agent import Agent
from core.llm_client import ChatResponse, OpenAICompatibleClient
import miniyu_web


# ============================================================
# HTTP 桩：字节流（模拟真实 requests.iter_content 的 bytes 输出）
# ============================================================

class _BytesStreamResp:
    """流式响应桩：iter_content 吐 UTF-8 字节块。

    模拟 Ollama：headers 无 charset（requests 会推断 ISO-8859-1，
    旧实现 decode_unicode=True 由此产生 mojibake）。
    """

    def __init__(self, chunks):
        self._chunks = chunks
        self.status_code = 200
        self.headers = {"Content-Type": "text/event-stream"}
        self.encoding = "ISO-8859-1"  # requests 对无 charset 的推断值

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=None, decode_unicode=False):
        yield from self._chunks


def _sse_bytes(*datas):
    """拼 OpenAI 风格 SSE 的 UTF-8 字节（最后补 [DONE]）"""
    text = "".join(
        f"data: {json.dumps(d, ensure_ascii=False)}\n\n" for d in datas
    ) + "data: [DONE]\n\n"
    return text.encode("utf-8")


def _delta(content=None, reasoning=None):
    d = {"choices": [{"delta": {}}]}
    if content:
        d["choices"][0]["delta"]["content"] = content
    if reasoning:
        d["choices"][0]["delta"]["reasoning"] = reasoning
    return d


class TestStreamUtf8Decoding:
    """修复验证：UTF-8 字节流 → 正常中文（旧实现产出 mojibake）"""

    def _client(self):
        return OpenAICompatibleClient(
            base_url="http://localhost:11434/v1", api_key="", model="m"
        )

    def test_utf8_bytes_decoded_correctly(self, monkeypatch):
        """含中文的 SSE 字节流 → token 输出正常中文"""
        raw = _sse_bytes(_delta("你好呀！"), _delta("我是本地模型。"))
        monkeypatch.setattr(
            "core.llm_client.requests.post",
            lambda *a, **k: _BytesStreamResp([raw]),
        )
        chunks = list(self._client().chat_stream([{"role": "user", "content": "hi"}]))
        text = "".join(c.text for c in chunks if c.finish_reason == "streaming")
        assert text == "你好呀！我是本地模型。"

    def test_latin1_inference_no_longer_mojibake(self, monkeypatch):
        """回归锁：resp.encoding=ISO-8859-1（Ollama 场景）也不产生乱码。

        旧实现 decode_unicode=True 按该编码解码，'你好' 变 'ä½ å¥½'。
        """
        raw = _sse_bytes(_delta("你好"))
        monkeypatch.setattr(
            "core.llm_client.requests.post",
            lambda *a, **k: _BytesStreamResp([raw]),
        )
        chunks = list(self._client().chat_stream([{"role": "user", "content": "hi"}]))
        text = "".join(c.text for c in chunks if c.finish_reason == "streaming")
        assert text == "你好"
        assert "ä½" not in text  # mojibake 特征串必须不出现

    def test_multibyte_char_split_across_chunks(self, monkeypatch):
        """网络块边界切在中文 3 字节中间 → 增量解码器仍正确拼出"""
        full = _sse_bytes(_delta("你好世界"))
        # 找到中文首字节位置切一刀：'你' 的 UTF-8 是 E4 BD A0，切在 E4 | BD A0...
        cut = full.index(b"\xe4\xbd\xa0") + 1
        monkeypatch.setattr(
            "core.llm_client.requests.post",
            lambda *a, **k: _BytesStreamResp([full[:cut], full[cut:]]),
        )
        chunks = list(self._client().chat_stream([{"role": "user", "content": "hi"}]))
        text = "".join(c.text for c in chunks if c.finish_reason == "streaming")
        assert text == "你好世界"

    def test_str_chunks_still_supported(self, monkeypatch):
        """桩/个别实现直接给 str：跳过解码器，原样使用"""
        sse_text = "".join(
            f"data: {json.dumps(_delta('你好'), ensure_ascii=False)}\n\n"
        ) + "data: [DONE]\n\n"
        monkeypatch.setattr(
            "core.llm_client.requests.post",
            lambda *a, **k: _BytesStreamResp([sse_text]),  # str 而非 bytes
        )
        chunks = list(self._client().chat_stream([{"role": "user", "content": "hi"}]))
        text = "".join(c.text for c in chunks if c.finish_reason == "streaming")
        assert text == "你好"


# ============================================================
# Agent.run_stream(stop_check=...)：流式打断
# ============================================================

class _StreamingFakeLLM:
    """假流式 LLM：逐 chunk 吐文本，最后正常收尾"""

    provider = "openai_compatible"
    supports_vision = False
    tool_free = True  # 纯对话模式（本地模型场景）
    server_tools = {"web_search": False}

    def __init__(self, tokens):
        self._tokens = tokens

    def chat_stream(self, messages, tools=None):
        for t in self._tokens:
            yield ChatResponse(text=t, finish_reason="streaming")
        yield ChatResponse(finish_reason="stop")


def _agent(tmp_path, tokens):
    return Agent(
        config={
            "llm": {"provider": "openai_compatible"},
            "agent": {"max_steps": 5, "confirm_high_risk": False},
            "memory": {"storage_dir": str(tmp_path)},
        },
        llm_client=_StreamingFakeLLM(tokens),
        confirm_handler=None,
    )


class TestRunStreamStop:
    """stop_check：chunk 间隙打断，保留部分文本"""

    def test_stop_mid_stream_keeps_partial_text(self, tmp_path):
        """第 3 个 chunk 后打断 → 已流出文本 + 停止标记，入会话并落盘"""
        tokens = ["春", "风", "拂", "面", "绿", "意", "浓"]
        agent = _agent(tmp_path, tokens)
        state = {"n": 0}

        def stop_check():
            state["n"] += 1
            return state["n"] > 3  # 第 4 次检查起请求停止

        chunks = list(agent.run_stream("写诗", stop_check=stop_check))
        streaming = [c for c in chunks if c.finish_reason == "streaming"]
        stops = [c for c in chunks if c.finish_reason == "stop"]

        # 打断生效：流式 token 少于全部，stop chunk 携带"部分文本+标记"
        assert len(streaming) < len(tokens)
        assert len(stops) == 1
        assert "已手动停止" in stops[0].text
        assert "春" in stops[0].text
        # 会话历史：assistant 消息含部分文本 + 停止标记，且已落盘
        msgs = agent.conversation.messages
        assert msgs[-1]["role"] == "assistant"
        assert "已手动停止" in msgs[-1]["content"]
        # 落盘可读：从磁盘重新加载当前会话，打断后的 assistant 消息已持久化
        cid = agent.sessions.current_id
        assert cid is not None
        reloaded = agent.sessions.load(cid)
        assert reloaded is not None and reloaded.messages
        assert reloaded.messages[-1]["role"] == "assistant"

    def test_stop_before_any_output(self, tmp_path):
        """step 开头就请求停止 → 只有停止标记，无部分文本"""
        agent = _agent(tmp_path, ["你好"])
        chunks = list(agent.run_stream("hi", stop_check=lambda: True))
        stops = [c for c in chunks if c.finish_reason == "stop"]
        assert len(stops) == 1
        assert stops[0].text.strip().startswith("⏹")
        assert agent.conversation.messages[-1]["role"] == "assistant"

    def test_no_stop_normal_flow_unchanged(self, tmp_path):
        """stop_check 恒 False → 正常完成（回归：不影响原路径）"""
        tokens = ["a", "b", "c"]
        agent = _agent(tmp_path, tokens)
        chunks = list(agent.run_stream("hi", stop_check=lambda: False))
        streaming = [c for c in chunks if c.finish_reason == "streaming"]
        assert "".join(c.text for c in streaming) == "abc"
        assert any(c.finish_reason == "stop" for c in chunks)
        assert "已手动停止" not in agent.conversation.messages[-1]["content"]

    def test_without_stop_check_unchanged(self, tmp_path):
        """不传 stop_check → 行为与旧签名完全一致（兼容既有调用方）"""
        agent = _agent(tmp_path, ["好"])
        chunks = list(agent.run_stream("hi"))
        assert any(c.finish_reason == "stop" for c in chunks)


# ============================================================
# Web 端 /task/<id>/stop：停止端点 + SSE 全链路
# ============================================================

class _SlowFakeAgent:
    """慢速假 Agent：模拟本地模型长时间流式输出（每 chunk 间隔），
    stop_check 被调用时立即停止（复刻真实 Agent 行为）"""

    def __init__(self):
        self.stop_event = threading.Event()
        self.model_name = "fake-local"

    def run_stream(self, message, stop_check=None):
        yield ChatResponse(text="正在", finish_reason="streaming")
        yield ChatResponse(text="生成", finish_reason="streaming")
        for _ in range(200):  # 模拟死循环长输出
            if stop_check is not None and stop_check():
                yield ChatResponse(text="⏹ 已手动停止生成。", finish_reason="stop")
                return
            time.sleep(0.02)
            yield ChatResponse(text="字", finish_reason="streaming")
        yield ChatResponse(text="完成", finish_reason="stop")


class TestStopEndpoint:

    @pytest.fixture()
    def app_client(self, monkeypatch):
        monkeypatch.setattr(miniyu_web, "_agent", _SlowFakeAgent())
        miniyu_web._tasks.clear()
        miniyu_web.app.config["TESTING"] = True
        with miniyu_web.app.test_client() as c:
            yield c

    def test_stop_unknown_task(self, app_client):
        resp = app_client.post("/task/nonexist/stop")
        assert resp.get_json()["ok"] is False

    def test_stop_running_task_interrupts(self, app_client):
        """POST /chat → 流式中 → POST stop → SSE done 携带停止标记，无死循环刷屏"""
        r = app_client.post("/chat", json={"message": "写长文"})
        task_id = r.get_json()["task_id"]

        # 让后台线程先流出几个 token
        time.sleep(0.15)

        stop_resp = app_client.post(f"/task/{task_id}/stop")
        assert stop_resp.get_json()["ok"] is True

        # 等待 SSE 终态（打断应在秒级生效，200 chunk × 0.02s ≈ 4s 不应跑满）
        events = []
        deadline = time.time() + 5
        with app_client.get(f"/task/{task_id}/events") as sse:
            for line in sse.response:
                if time.time() > deadline:
                    break
                text = line.decode("utf-8") if isinstance(line, bytes) else line
                if text.startswith("data: "):
                    events.append(json.loads(text[6:]))
                if events and events[-1].get("type") in ("done", "error"):
                    break

        assert events, "SSE 必须有事件"
        final = events[-1]
        assert final["type"] == "done"
        assert "已手动停止" in final["result"]
        # 打断及时性：不该把 200 个'字'全吐完
        tokens = [e for e in events if e.get("type") == "token"]
        assert len(tokens) < 100

    def test_stop_finished_task_rejected(self, app_client):
        """已结束任务再停 → ok False"""
        miniyu_web._tasks["deadbeef"] = {
            "status": "done", "result": "x", "events": None,
            "stop_event": threading.Event(),
        }
        resp = app_client.post("/task/deadbeef/stop")
        assert resp.get_json()["ok"] is False
