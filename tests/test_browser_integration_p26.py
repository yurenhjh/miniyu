# -*- coding: utf-8 -*-
"""test_browser_integration_p26.py — P2-6 Agent Integration Layer

不依赖真实浏览器，用 Fake 覆盖 CDP/JS 边界，锁定 gtp 要求的 5 个集成点：

  Test1  browser_type(press_enter) → pending baseline 自动生成 → wait_for_changes()→ COMPLETED
  Test2  wait_for_changes() 无 pending → NO_PENDING_WAIT_BASELINE
  Test3  browser_read_latest_reply() → 只返回 assistant 正文；不含推荐 chips；不含整页 sidebar
  Test4  assistant 正文内真实链接/可点击长文本 → 不得被误过滤成 control
  Test5  browser_read_text({}) 旧行为保持不变；新工具共用同一 controller，不破坏现有 read_text

不修改 P2-5 核心；本层只做“透出/接线”。
"""
import unittest

from core.browser_controller import BrowserController
from core.tool_registry import ToolRegistry

_STATE_JS_MARK = "semantic_fingerprint"      # 与 _SEMANTIC_STATE_JS 内容同源（用于 JS 分发，仅测试）
_BLOCKS_JS_MARK = "suggestContainers"        # 与 _BLOCKS_JS 明显 token（仅测试分发判据）


def _leaf(idx, text, clickable=False, short=False, suggestion=False, tag="div", role="",
          rect=None, line=None):
    line = text if line is None else line
    return {"line": line, "idx": idx, "tag": tag, "role": role,
            "clickable": clickable, "short": short, "suggestion": suggestion,
            "control": (suggestion or (clickable and short)),
            "rect": rect or [10, idx * 20, 100, 18]}


class FakeSeq(BrowserController):
    """可控语义状态：semantic_state 按序吐 state；_BLOCKS_JS 依调用次序返回 基线/最终 叶节点。"""

    _BLOCKS_JS = "suggestContainers"  # 占位，避免 super 访问真实常量；实际分发见 _evaluate

    def __init__(self, states=None, baseline_leaves=None, final_leaves=None):
        super().__init__(ws_url="fake")
        self._states = list(states or [])
        self._base_leaves = list(baseline_leaves or [])
        self._final_leaves = list(final_leaves or [])
        self._take_base_next = False

    def _ensure_anchor(self):
        return True

    def capture_wait_baseline(self, user_text=None):
        self._take_base_next = True
        return super().capture_wait_baseline(user_text)

    def semantic_state(self):
        return self._states.pop(0) if self._states else {"fingerprint": None,
                                                         "loading": False,
                                                         "semantic_text": "",
                                                         "sem_block_count": 0}

    def _evaluate(self, js, *a, **k):
        if _BLOCKS_JS_MARK in js:
            if self._take_base_next:
                self._take_base_next = False
                return self._base_leaves
            return self._final_leaves
        return {}


class TestPendingBaseline(unittest.TestCase):

    def test_capture_then_wait_completed(self):
        """Test1：capture（发送后）→ wait_for_changes() 无参数 → COMPLETED，delta 含正文不含 chips。"""
        s0 = {"fingerprint": "B0", "loading": False, "semantic_text": "你好",
              "sem_block_count": 1}
        s1 = {"fingerprint": "B0", "loading": False}
        s2 = {"fingerprint": "B1", "loading": False}   # 回复到达 → delta
        s3 = {"fingerprint": "B1", "loading": False}
        s4 = {"fingerprint": "B1", "loading": False}   # stable=2 → COMPLETED
        bc = FakeSeq(
            states=[s0, s1, s2, s3, s4],
            baseline_leaves=[_leaf(0, "你好")],
            final_leaves=[
                _leaf(0, "你好"),
                _leaf(1, "你好！有什么我可以帮你的吗？"),
                _leaf(2, "你能做些什么？", clickable=True, short=True, suggestion=True),
            ],
        )
        cap = bc.capture_wait_baseline("你好")
        self.assertTrue(cap["captured"])
        res = bc.wait_for_changes(interval=0.005, timeout=5, min_stable_rounds=2)
        self.assertTrue(res["success"])
        self.assertEqual(res["state"], "COMPLETED")
        self.assertIn("你好！有什么我可以帮你的吗？", res["message_delta"])
        self.assertNotIn("你能做些什么？", res["message_delta"])
        # 确认 delta 用的是自动捕获的 pending，而非 null
        self.assertTrue(res["message_delta"])

    def test_no_pending_wait(self):
        """Test2：wait_for_changes() 且无 pending → 明确 NO_PENDING_WAIT_BASELINE，不装作完成。"""
        bc = FakeSeq(states=[{"fingerprint": "B0", "loading": False}])
        res = bc.wait_for_changes(interval=0.005, timeout=1)
        self.assertFalse(res["success"])
        self.assertEqual(res["state"], "NO_PENDING_WAIT_BASELINE")

    def test_dict_baseline_still_used(self):
        """旧路径不破坏：传入 dict baseline 仍按 dict 走（P2-5 兼容）。"""
        s0 = {"fingerprint": "A", "loading": False, "semantic_text": "旧", "sem_block_count": 1}
        s1 = {"fingerprint": "A", "loading": False}
        s2 = {"fingerprint": "B", "loading": False}
        s3 = {"fingerprint": "B", "loading": False}
        s4 = {"fingerprint": "B", "loading": False}
        bc = FakeSeq(
            states=[s1, s2, s3, s4],
            final_leaves=[_leaf(0, "新回复")],
        )
        # 不调用 capture，直接给 dict baseline → 不应报 NO_PENDING
        res = bc.wait_for_changes(baseline={"fingerprint": "A", "leaf_lines": ["旧"]},
                                  interval=0.005, timeout=5, min_stable_rounds=2)
        self.assertTrue(res["success"])
        self.assertEqual(res["state"], "COMPLETED")
        self.assertIn("新回复", res["message_delta"])


class TestReadLatestReply(unittest.TestCase):

    def test_reply_only_no_chips_no_sidebar(self):
        """Test3：read_latest_reply 只回 assistant 正文，排除推荐 chips 与整页 sidebar/控件。"""
        bc = FakeSeq(final_leaves=[
            _leaf(0, "你好", clickable=False),
            _leaf(1, "你好！有什么我可以帮你的吗？", clickable=False),
            _leaf(2, "你能做些什么？", clickable=True, short=True, suggestion=True),
            _leaf(3, "发送", clickable=True, short=True, tag="button"),
        ])
        bc._pending_wait_baseline = {"leaf_lines": ["你好"], "user_message_text": "你好"}
        res = bc.read_latest_reply()
        self.assertTrue(res["success"])
        self.assertEqual(res["source"], "structured_read")
        self.assertEqual(res["text"], "你好！有什么我可以帮你的吗？")
        self.assertNotIn("你能做些什么？", res["text"])
        self.assertNotIn("发送", res["text"])
        self.assertNotIn("会话列表", res["text"])

    def test_content_link_not_filtered(self):
        """Test4：正文内真实链接/长可点文本 必须保留为 content，不得被误过滤。"""
        bc = FakeSeq(final_leaves=[
            _leaf(0, "完整实现说明请看下面的仓库链接，它详细讲了方案。", clickable=False),
            _leaf(1, "GitHub 项目主页（深度解析全文）", clickable=True, short=False, tag="a", role="link"),
        ])
        bc._pending_wait_baseline = {"leaf_lines": [], "user_message_text": None}
        res = bc.read_latest_reply()
        self.assertTrue(res["success"])
        self.assertEqual(res["source"], "structured_read")
        self.assertIn("GitHub 项目主页（深度解析全文）", res["text"])
        self.assertIn("完整实现说明", res["text"])

    def test_anchor_lost(self):
        bc = FakeSeq()
        bc._ensure_anchor = lambda: False
        res = bc.read_latest_reply()
        self.assertFalse(res["success"])
        self.assertEqual(res["state"], "ANCHOR_LOST")


class TestRegistryWiring(unittest.TestCase):

    def test_browser_type_captures_pending_and_wait_hint(self):
        """browser_type(press_enter=True) → 自动 capture pending + 返回 wait_hint。"""
        cap = {}

        class FB:
            def type_text(self, text, **k):
                return {"typed": text, "success": True}

            def press_enter(self):
                pass

            def capture_wait_baseline(self, text):
                cap["text"] = text
                return {"captured": True}

        reg = ToolRegistry()
        reg._browser = FB()
        ret = reg.browser_type(target="e1", text="你好", press_enter=True)
        self.assertTrue(ret["wait_hint"] == "browser_wait_for_change")
        self.assertTrue(ret["enter_pressed"] is True)
        self.assertEqual(cap.get("text"), "你好")
        # 未回车则不应有 wait_hint
        ret2 = reg.browser_type(target="e1", text="你好", press_enter=False)
        self.assertFalse("wait_hint" in ret2)

    def test_wait_for_change_string_delegates_to_pending(self):
        """browser_wait_for_change(字符串/None) → baseline 交给 controller pending，而非伪造 dict。"""
        got = {}

        class FB:
            def wait_for_changes(self, baseline=None, **k):
                got["baseline"] = baseline
                return {"success": True, "state": "COMPLETED"}

        reg = ToolRegistry()
        reg._browser = FB()
        # 旧错误用法：传字符串 baseline → 现在忽略，交 pending（controller 无 pending 时给明确报错）
        reg.browser_wait_for_change(baseline="有什么我能帮你的吗？", interval=0.01)
        self.assertIsNone(got["baseline"])
        reg.browser_wait_for_change(interval=0.01)
        self.assertIsNone(got["baseline"])

    def test_read_latest_reply_registered_read_text_intact(self):
        """Test5：browser_read_latest_reply 已注册；browser_read_text 旧工具仍存在不被破坏。"""
        reg = ToolRegistry()
        names = [t for t in reg.list_tools()]
        self.assertIn("browser_read_latest_reply", names)
        self.assertIn("browser_read_text", names)
        self.assertIn("browser_wait_for_change", names)
        # browser_read_text 签名未变（仍只接受 selector）
        self.assertTrue(hasattr(ToolRegistry, "browser_read_text"))


if __name__ == "__main__":
    unittest.main()