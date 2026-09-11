"""
test_agent.py：Agent 编排层单元测试

使用离线脑（DeterministicBrain）进行测试，不依赖网络和 API key。
"""

from pathlib import Path

import pytest

from core.agent import Agent
from core.llm_client import ChatResponse, DeterministicBrain
from core.os_service_api import OSServiceAPI
from core.safety import ConfirmationDenied


class TestAgent:
    """Agent 核心功能测试"""

    def setup_method(self):
        # storage_dir 指向临时目录：测试不得读写真实 conversations/（曾把 stub
        # 回复写进用户会话，导致浏览器测试历史被污染）
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="miniyu_test_")
        self.agent = Agent(
            config={
                "llm": {"provider": "deterministic"},
                "agent": {"max_steps": 15, "confirm_high_risk": False, "history_window": 20},
                "memory": {"storage_dir": self._tmpdir},
            },
            confirm_handler=None,
        )

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_empty_input(self):
        result = self.agent.run("")
        assert result == "请输入指令。"

    def test_whitespace_input(self):
        result = self.agent.run("   ")
        assert result == "请输入指令。"

    def test_help(self):
        result = self.agent.run("你能做什么")
        assert "文件操作" in result

    def test_disk_space(self):
        """磁盘空间→离线脑返回 tool_call→执行→返回结果"""
        result = self.agent.run("C盘空间")
        assert result is not None
        assert len(result) > 0

    def test_network_status(self):
        result = self.agent.run("网络状态")
        assert result is not None

    def test_list_processes(self):
        result = self.agent.run("查看进程")
        assert result is not None

    def test_reset(self):
        self.agent.run("C盘空间")
        self.agent.reset()
        assert len(self.agent.conversation.messages) == 0

    def test_provider(self):
        assert self.agent.provider == "deterministic"

    def test_unknown_instruction(self):
        result = self.agent.run("今天天气怎么样")
        assert "帮助" in result


class TestAgentWithConfirm:
    """Agent 高危确认测试"""

    def test_high_risk_denied(self, tmp_path):
        """高危操作拒绝→返回取消信息"""
        def deny_handler(pv):
            raise ConfirmationDenied(pv.get("name", ""))

        agent = Agent(
            config={
                "llm": {"provider": "deterministic"},
                "agent": {"max_steps": 15, "confirm_high_risk": True, "history_window": 20},
                "memory": {"storage_dir": str(tmp_path)},
            },
            confirm_handler=deny_handler,
        )
        # 离线脑识别"结束进程"→ 触发 kill_process (高危)
        result = agent.run("结束进程 1234")
        assert "拒绝" in result or "取消" in result

    def test_high_risk_allowed(self, tmp_path):
        """高危操作放行→正常执行"""
        def allow_handler(pv):
            return True

        agent = Agent(
            config={
                "llm": {"provider": "deterministic"},
                "agent": {"max_steps": 15, "confirm_high_risk": True, "history_window": 20},
                "memory": {"storage_dir": str(tmp_path)},
            },
            confirm_handler=allow_handler,
        )
        result = agent.run("结束进程 1234")
        assert result is not None


class TestAgentMaxSteps:
    """最大步数测试"""

    def test_max_steps_guard(self, tmp_path):
        """超步数自动终止"""
        # 用离线脑，max_steps=1，能正常完成的任务不受影响
        # 正常情况下离线脑1步就完成
        agent = Agent(
            config={
                "llm": {"provider": "deterministic"},
                "agent": {"max_steps": 1, "confirm_high_risk": False, "history_window": 20},
                "memory": {"storage_dir": str(tmp_path)},
            },
        )
        result = agent.run("C盘空间")
        # 离线脑1步完成，不会触发超步数
        assert result is not None


class FakeOpenAIClient:
    """模拟支持原生 tool_calls 的真实 LLM（provider=openai_compatible）"""

    provider = "openai_compatible"
    supports_vision = False

    def __init__(self, script=None):
        self.script = list(script or [])
        self.chat_calls = []
        self.stream_calls = []

    def chat(self, messages, tools=None):
        self.chat_calls.append({"messages": messages, "tools": tools})
        if self.script:
            return self.script.pop(0)
        return ChatResponse(text="完成", finish_reason="stop")

    def chat_stream(self, messages, tools=None):
        self.stream_calls.append({"messages": messages, "tools": tools})
        if self.script:
            resp = self.script.pop(0)
            if resp.tool_calls:
                yield ChatResponse(tool_calls=resp.tool_calls, finish_reason="tool_calls")
                return
        # 模拟真实 SSE 纯文本流：逐 token 后自然结束（无 stop 终止块）
        yield ChatResponse(text="完成", finish_reason="streaming")
        return


class TestAgentRealLLMIntegration:
    """真实 LLM（openai_compatible）链路行为：system prompt / 流式收尾 / 多工具配对"""

    def _build(self, fake, tmp_path, max_steps=3):
        return Agent(
            config={
                "llm": {"provider": "openai_compatible"},
                "agent": {"max_steps": max_steps, "confirm_high_risk": False, "history_window": 20},
                "memory": {"storage_dir": str(tmp_path)},
            },
            llm_client=fake,
        )

    def test_system_prompt_injected(self, tmp_path):
        """真实 LLM 请求首条是 system 且含 miniyu 人设"""
        fake = FakeOpenAIClient([
            ChatResponse(
                tool_calls=[{"name": "current_directory", "arguments": {}}],
                finish_reason="tool_calls",
            ),
            ChatResponse(text="搞定", finish_reason="stop"),
        ])
        agent = self._build(fake, tmp_path)
        result = agent.run("看看当前目录")
        assert result == "搞定"
        first = fake.chat_calls[0]["messages"][0]
        assert first["role"] == "system"
        assert "miniyu" in first["content"]

    def test_no_system_for_deterministic(self, tmp_path):
        """离线（deterministic）模式不注入 system，避免干扰关键词匹配"""
        fake = FakeOpenAIClient([
            ChatResponse(
                tool_calls=[{"name": "current_directory", "arguments": {}}],
                finish_reason="tool_calls",
            ),
            ChatResponse(text="完成", finish_reason="stop"),
        ])
        fake.provider = "deterministic"
        agent = self._build(fake, tmp_path)
        agent.run("看看当前目录")
        first = fake.chat_calls[0]["messages"][0]
        assert first["role"] != "system"
        assert first["role"] == "user"

    def test_stream_pure_text_terminates(self, tmp_path):
        """纯文本流式（无 stop 块）也能终止：只调一轮、末尾有 stop chunk"""
        fake = FakeOpenAIClient([])
        agent = self._build(fake, tmp_path)
        chunks = list(agent.run_stream("你好"))
        assert len(fake.stream_calls) == 1  # 修复前会一直重调直到 max_steps
        assert chunks[0].finish_reason == "streaming"
        assert chunks[-1].finish_reason == "stop"
        # 流式文本已写回会话历史
        assert agent.conversation.messages[-1]["role"] == "assistant"
        assert agent.conversation.messages[-1]["content"] == "完成"

    def test_multi_tool_call_id_pairing(self, tmp_path):
        """一次返回多条 tool_call 时，每条结果回填各自 id（不共享第一个 id）"""
        fake = FakeOpenAIClient([
            ChatResponse(
                tool_calls=[
                    {"name": "current_directory", "arguments": {}},
                    {"name": "file_exists", "arguments": {"path": str(Path.cwd())}},
                ],
                finish_reason="tool_calls",
            ),
            ChatResponse(text="都查好了", finish_reason="stop"),
        ])
        agent = self._build(fake, tmp_path)
        result = agent.run("并行查一下目录和文件存在性")
        assert result == "都查好了"

        conv = agent.conversation
        asst = [m for m in conv.messages if m.get("tool_calls")][0]
        tool_msgs = [m for m in conv.messages if m["role"] == "tool"]
        ids = [t["id"] for t in asst["tool_calls"]]
        assert len(ids) == 2 and ids[0] != ids[1]
        # 修复前所有结果都贴第一个 id，这里断言按序一一配对
        assert [m["tool_call_id"] for m in tool_msgs] == ids
        assert [m["name"] for m in tool_msgs] == ["current_directory", "file_exists"]


class TestSandboxGuard:
    """SecuritySandbox 危险指令硬拦截接入 Agent 工具执行链的测试"""

    def setup_method(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="miniyu_test_sb_")
        # confirm_high_risk=False（全自动档）：证明拦截来自沙箱硬拦截而非确认门
        self.agent = Agent(
            config={
                "llm": {"provider": "deterministic"},
                "agent": {"max_steps": 15, "confirm_high_risk": False, "history_window": 20},
                "memory": {"storage_dir": self._tmpdir},
            },
            confirm_handler=None,
        )
        assert self.agent.coordinator is not None

    def teardown_method(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _call(self, cmd: str) -> dict:
        return self.agent._execute_one("run_command", {"cmd": cmd})

    @pytest.mark.parametrize("cmd,keyword", [
        ("rm -rf /", "危险指令"),
        ("rm -rf ~/Desktop", "危险指令"),
        ("shutdown -h now", "危险指令"),
        ("format c:", "危险指令"),
        ("dd if=/dev/zero of=/dev/sda", "危险指令"),
        ("mkfs.ext4 /dev/sdb1", "危险指令"),
        ("fdisk /dev/sda", "危险指令"),
        ("Stop-Computer -Force", "危险指令"),
        ("Restart-Computer", "危险指令"),
        ("Remove-Item -Recurse -Force C:\\", "危险指令"),
    ])
    def test_dangerous_cmd_blocked(self, cmd, keyword):
        result = self._call(cmd)
        assert result["success"] is False
        assert keyword in result["error"]

    def test_normal_cmd_not_blocked(self):
        result = self._call("echo miniyu")
        # 全自动档无确认门 → 应真实执行成功，说明硬拦截只针对危险指令
        assert result.get("success") is True, result

    def test_no_coordinator_keeps_old_behavior(self):
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="miniyu_test_sb2_")
        try:
            agent = Agent(
                config={
                    "llm": {"provider": "deterministic"},
                    "agent": {"max_steps": 15, "confirm_high_risk": False,
                              "coordinator": {"enabled": False}},
                    "memory": {"storage_dir": tmpdir},
                },
                confirm_handler=None,
            )
            assert agent.coordinator is None
            result = agent._execute_one("run_command", {"cmd": "echo hi"})
            assert result.get("success") is True, result
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_non_command_tool_untouched(self):
        result = self.agent._execute_one("current_directory", {})
        assert result.get("success") is True, result