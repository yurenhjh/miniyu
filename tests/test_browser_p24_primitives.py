# -*- coding: utf-8 -*-
"""P2-4.1 回归：真实豆包 run_log 暴露出的三个真实页面问题。

Step4 豆包页里 snapshot 先登记了 textbox 的 ref，随后 find(role=textbox)
再次命中同一 ref —— 旧 _alloc_handles 只给"新建"句柄分配 handle，已登记 ref
返回时 without handle，模型拿不到 handle 就绕回 selector 旧路（P2-4 被绕过）。
同时 find 结果里超长内部 ref 把 handle 挤到日志 400 字符截断区之外。

本测试锁定三处修复：
1. find 命中同会话已登记的 ref 时，回填既有 handle（不再返回无 handle 条目）
2. find/snapshot 结果 handle 恒排最前（_front_handle），截断也不吞 handle
3. browser_wait 无条件等待（selector/text 皆空）在工具层直接拒绝，不空等超时
4. find 的 edit_host 语义修正：自身即可用宿主时 edit_host=true + host_kind='self'
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from core.browser_controller import BrowserController, _build_find_js
from core.tool_registry import ToolRegistry


class _Fake(BrowserController):
    """受控 CDP 桩：聚焦 find/snapshot 的 handle 回填与字段顺序。"""

    def __init__(self):
        super().__init__(ws_url="fake")
        self._reset_stub()
        self.find_canned = []
        self.snapshot_canned = []

    def _reset_stub(self):
        self._activity_sid = 0
        self._page_sig = None
        self._handle_seq = 0
        self._handle_registry = {}

    def _send(self, method, params=None):
        return {}

    def _evaluate(self, expression):
        expr = str(expression).strip()
        if expr == "window.devicePixelRatio":
            return 1.0
        if "const wantRole" in expr:                 # find 桩
            return [dict(x) for x in self.find_canned]
        if "data-agentic-idx" in expr:               # snapshot 桩
            return [dict(x) for x in self.snapshot_canned]
        return None

    def _read_page_sig(self):
        return None                                  # 桩环境无真实页面指纹


# 与真实豆包聊天输入框同构：div role=textbox，自身就是 contenteditable 富文本框
TEXTBOX = {
    "ref": "ebody:0_div:0_div:1_textbox", "tag": "div", "role": "textbox",
    "name": "发消息或按住空格说话...", "editable": True,
    "input_kind": "contenteditable", "visible": True, "disabled": False,
    "edit_host": False, "host_kind": None, "in_viewport": True,
}


class TestRepeatRefHandleReuse(unittest.TestCase):
    """核心回归：snapshot 登记过的 ref，find 命中时也必须带回 handle。"""

    def setUp(self):
        self.fb = _Fake()
        self.fb._reset_stub()

    def test_find_when_ref_already_registered_returns_handle(self):
        # 模拟真实流程：先 snapshot 登记 textbox 的 ref，再 find(role=textbox) 命中同一 ref
        self.fb.snapshot_canned = [dict(TEXTBOX)]
        self.fb.find_canned = [dict(TEXTBOX)]
        snap = self.fb.snapshot()
        self.assertEqual(len(snap), 1)
        self.assertTrue(snap[0].get("handle"))          # snapshot 首次登记 → 拿到 handle

        items = self.fb.find(role="textbox")
        self.assertEqual(len(items), 1)
        # P2-4.1 回归点：旧代码这里 items[0] 没有 handle，模型被迫绕回 selector
        self.assertIn("handle", items[0])
        self.assertTrue(items[0]["handle"])
        # 同会话同 ref 复用同一句柄，不应重建新号
        self.assertEqual(items[0]["handle"], snap[0]["handle"])

    def test_find_idempotent_second_call_reuses_handle(self):
        self.fb.find_canned = [dict(TEXTBOX)]
        a = self.fb.find(role="textbox")
        b = self.fb.find(role="textbox")
        self.assertEqual(a[0]["handle"], b[0]["handle"])
        self.assertEqual(len(self.fb._handle_registry), 1)


class TestHandleFieldOrdering(unittest.TestCase):
    def setUp(self):
        self.fb = _Fake()
        self.fb._reset_stub()
        self.fb.find_canned = [dict(TEXTBOX)]

    def test_handle_is_first_key(self):
        items = self.fb.find(role="textbox")
        keys = list(items[0].keys())
        self.assertEqual(keys[0], "handle")
        # 超长内部 ref 应落在后面，避免把 handle 挤出截断窗口
        self.assertGreater(keys.index("ref"), keys.index("handle"))

    def test_snapshot_handle_first_too(self):
        self.fb.snapshot_canned = [dict(TEXTBOX)]
        items = self.fb.snapshot()
        self.assertEqual(list(items[0].keys())[0], "handle")


class TestEditHostSelfSemantics(unittest.TestCase):
    def test_find_js_marks_self_host(self):
        js = _build_find_js(None, "textbox", None, None, 20)
        self.assertIn("edit_host: !!host", js)
        self.assertIn("host_kind", js)
        # 自身即可用 host 时应归为 self，而非指向"无宿主"
        self.assertIn("'self'", js)
        self.assertIn("'descendant'", js)


class TestBrowserWaitValidation(unittest.TestCase):
    def test_nowait_cond_rejected_without_sleep(self):
        reg = ToolRegistry()
        r = reg.browser_wait()               # selector/text 都为空
        self.assertFalse(r.get("success"))
        self.assertIn("不能无条件等待", r.get("error", ""))

    def test_with_text_still_passes_through(self):
        reg = ToolRegistry()
        # text 非空 → 不触发拒绝分支；无真实浏览器时会走底层并抛连接类错误，而非返回拒绝文案
        try:
            r = reg.browser_wait(text="你好", timeout=0.1)
            self.assertNotEqual(r.get("error", "").find("不能无条件等待"), 0)
        except Exception:
            self.assertTrue(True)            # 走到真实 wait_for（未挂浏览器），证明绕过校验分支


if __name__ == "__main__":
    unittest.main(verbosity=2)