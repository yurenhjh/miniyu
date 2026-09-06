"""
test_conversation.py：对话记忆单元测试
"""

import os
import tempfile
import pytest

from core.conversation import Conversation, SessionManager


class TestConversation:
    """对话记忆核心功能测试"""

    def test_add_user(self):
        conv = Conversation()
        conv.add_user("你好")
        assert len(conv.messages) == 1
        assert conv.messages[0]["role"] == "user"
        assert conv.messages[0]["content"] == "你好"

    def test_add_assistant_text(self):
        conv = Conversation()
        conv.add_assistant(content="好的")
        assert conv.messages[-1]["role"] == "assistant"
        assert conv.messages[-1]["content"] == "好的"

    def test_add_assistant_tool_calls(self):
        conv = Conversation()
        conv.add_assistant(tool_calls=[
            {"name": "copy_file", "arguments": {"src": "a.txt", "dest": "b.txt"}},
        ])
        msg = conv.messages[-1]
        assert msg["role"] == "assistant"
        assert msg["tool_calls"][0]["function"]["name"] == "copy_file"

    def test_add_tool_result(self):
        conv = Conversation()
        conv.add_assistant(tool_calls=[{"name": "test", "arguments": {}}])
        tc_id = conv.get_last_tool_call_id()
        conv.add_tool_result(tc_id, "test", {"success": True, "result": "ok"})
        assert conv.messages[-1]["role"] == "tool"
        assert conv.messages[-1]["name"] == "test"

    def test_get_last_tool_calls(self):
        conv = Conversation()
        conv.add_assistant(tool_calls=[
            {"name": "disk_usage", "arguments": {"path": "C:"}},
        ])
        calls = conv.get_last_tool_calls()
        assert len(calls) == 1
        assert calls[0]["name"] == "disk_usage"
        assert calls[0]["arguments"]["path"] == "C:"

    def test_sliding_window(self):
        conv = Conversation(window_size=3)
        for i in range(5):
            conv.add_user(f"msg{i}")
        window = conv.get_window()
        assert len(window) == 3
        assert window[0]["content"] == "msg2"

    def test_clear(self):
        conv = Conversation()
        conv.add_user("hello")
        conv.clear()
        assert len(conv.messages) == 0

    def test_add_image(self):
        conv = Conversation()
        conv.add_user("截图里有什么？")
        conv.add_image("base64data123")
        content = conv.messages[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "base64data123" in content[1]["image_url"]["url"]

    # ============================================================
    # 序列化 & 标题生成
    # ============================================================

    def test_auto_title(self):
        conv = Conversation()
        conv.add_user("帮我整理桌面文件")
        assert conv.title == "帮我整理桌面文件"

    def test_to_dict_and_from_dict(self):
        conv = Conversation()
        conv.add_user("你好")
        conv.add_assistant(content="有什么可以帮你的？")
        data = conv.to_dict()
        assert data["title"] == "你好"
        assert len(data["messages"]) == 2

        restored = Conversation.from_dict(data)
        assert restored.title == "你好"
        assert len(restored.messages) == 2
        assert restored.messages[0]["role"] == "user"

    def test_to_json_and_from_json(self):
        conv = Conversation()
        conv.add_user("你好")
        json_str = conv.to_json()
        restored = Conversation.from_json(json_str)
        assert restored.title == "你好"
        assert restored.messages[0]["content"] == "你好"

    def test_delete_last_turn(self):
        conv = Conversation()
        conv.add_user("你好")
        conv.add_assistant(content="你好！")
        conv.add_user("磁盘空间")
        conv.add_assistant(content="C盘剩余100GB")
        assert len(conv.messages) == 4
        conv.delete_last_turn()
        assert len(conv.messages) == 2
        assert conv.messages[-1]["content"] == "你好！"

    def test_summarized_window(self):
        conv = Conversation(window_size=3)
        for i in range(5):
            conv.add_user(f"msg{i}")
            conv.add_assistant(content=f"回复{i}")
        result = conv.get_summarized_window()
        # 3 条窗口 + 1 条摘要 = 4 条
        assert len(result) == 4
        assert result[0]["role"] == "system"  # 摘要消息
        assert "摘要" in result[0]["content"]


class TestSessionManager:
    """多会话管理器测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """每个测试使用临时目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            self.tmpdir = tmpdir
            self.sm = SessionManager(storage_dir=tmpdir)
            yield

    def test_create_and_current(self):
        sid = self.sm.create()
        assert sid is not None
        assert self.sm.current_id == sid
        assert self.sm.current is not None

    def test_create_with_title(self):
        sid = self.sm.create(title="工作会话")
        assert self.sm.current.title == "工作会话"

    def test_switch(self):
        sid1 = self.sm.create("会话1")
        sid2 = self.sm.create("会话2")
        assert self.sm.current_id == sid2
        self.sm.switch(sid1)
        assert self.sm.current_id == sid1
        assert self.sm.current.title == "会话1"

    def test_switch_nonexistent(self):
        result = self.sm.switch("nonexistent")
        assert result is False

    def test_delete(self):
        sid1 = self.sm.create("会话1")
        sid2 = self.sm.create("会话2")
        assert self.sm.delete(sid1) is True
        assert sid1 not in self.sm._sessions

    def test_delete_current_switches(self):
        sid1 = self.sm.create("会话1")
        sid2 = self.sm.create("会话2")
        assert self.sm.current_id == sid2
        self.sm.delete(sid2)
        assert self.sm.current_id == sid1

    def test_rename(self):
        sid = self.sm.create("旧标题")
        assert self.sm.rename(sid, "新标题") is True
        assert self.sm.current.title == "新标题"

    def test_list(self):
        self.sm.create("会话A")
        self.sm.create("会话B")
        sessions = self.sm.list()
        assert len(sessions) == 2
        titles = {s["title"] for s in sessions}
        assert titles == {"会话A", "会话B"}

    def test_ensure_current_creates_when_empty(self):
        assert self.sm.current is None
        conv = self.sm.ensure_current()
        assert conv is not None
        assert self.sm.current_id is not None

    def test_persistence(self):
        """创建会话 → 保存 → 重新加载 → 验证"""
        sid = self.sm.create("持久化测试")
        self.sm.current.add_user("你好")
        self.sm.save_all()

        # 新建一个 SessionManager 重新加载
        sm2 = SessionManager(storage_dir=self.tmpdir)
        sm2.load_all()
        assert sm2.current_id == sid
        assert sm2.current.title == "持久化测试"
        assert len(sm2.current.messages) == 1
        assert sm2.current.messages[0]["content"] == "你好"

    def test_multiple_sessions_persistence(self):
        """多个会话的持久化"""
        sids = []
        for i in range(3):
            sid = self.sm.create(f"会话{i}")
            self.sm.current.add_user(f"消息{i}")
            sids.append(sid)
        self.sm.save_all()

        sm2 = SessionManager(storage_dir=self.tmpdir)
        sm2.load_all()
        assert len(sm2.list()) == 3
        # 验证每个会话
        for sid in sids:
            sm2.switch(sid)
            assert sm2.current.title.startswith("会话")
            assert len(sm2.current.messages) == 1

    def test_list_ordering(self):
        """会话列表按更新时间降序"""
        import time
        sid1 = self.sm.create("旧")
        time.sleep(0.01)
        sid2 = self.sm.create("新")
        ids = self.sm.list_ids()
        assert ids[0] == sid2  # 最新的在最前
        assert ids[1] == sid1


class TestConversationMultiToolCall:
    """多工具并行时 tool_call_id 配对（真实 LLM 一次返回多条 tool_call 用）"""

    def test_last_tool_call_ids_two(self):
        conv = Conversation()
        conv.add_assistant(tool_calls=[
            {"name": "current_directory", "arguments": {}},
            {"name": "file_exists", "arguments": {"path": "C:/"}},
        ])
        ids = conv.last_tool_call_ids()
        assert len(ids) == 2
        assert ids[0] != ids[1]  # 两条 tool_call 各自独立 id
        stored = [tc["id"] for tc in conv.messages[-1]["tool_calls"]]
        assert ids == stored  # 按序返回

    def test_get_last_tool_call_id_compat(self):
        """旧接口仍返回第一条（兼容既有调用）"""
        conv = Conversation()
        conv.add_assistant(tool_calls=[
            {"name": "disk_usage", "arguments": {"path": "C:"}},
            {"name": "list_processes", "arguments": {}},
        ])
        assert conv.get_last_tool_call_id() == conv.last_tool_call_ids()[0]

    def test_empty_no_tool_calls(self):
        conv = Conversation()
        conv.add_user("你好")
        assert conv.last_tool_call_ids() == []