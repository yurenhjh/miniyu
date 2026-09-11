# -*- coding: utf-8 -*-
"""P2-3 Target Handle 层测试。

背景：豆包 E2E 里 browser_find 返回的长 internal ref（ebody:0_div:...）被模型复刻
时丢尾字符，导致 type/click 指向错误元素。本阶段为浏览器目标引入短生命周期 handle
（e1/e2/e3），把长 ref 收敛到程序内部，模型只操作 handle。

覆盖：
- snapshot / find / inspect 返回的每条元素都附带短 handle（互不重复）
- handle → 内部 long ref 解析：click / type 用 handle 能命中
- stale handle：页面导航/变化后旧 handle 失效，禁止硬用
- 页面变化后新 session 重新编号，新 handle 不与旧 handle 冲突
- Tool 层拒绝"长路径型内部 ref"（要求改用 handle），短语义 ref 保持向后兼容
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.browser_controller import BrowserController, SoMStaleError, _SNAPSHOT_JS, _INSPECT_JS
from core.uitarget import is_long_internal_ref, is_ref
from core.tool_registry import ToolRegistry


# 与真实豆包同构：长路径型 stableId + 短语义 ref 两种形态
LONG_REF = "ebody:0_div:0_div:0_div:0_main:0_div:0_div:1_div:0_div:0_div:1_"
SHORT_REF = "eid:fnd_btn"


class FakeBrowser(BrowserController):
    """受控 CDP mock：支持 snapshot / find / inspect 生成 handle，并可切换页面指纹模拟失效。"""

    def __init__(self, url="https://example.com/page", dpr=1.0, dom_len=1000):
        super().__init__(ws_url="fake")
        self.url = url
        self.dpr = dpr
        self.dom_len = dom_len
        self._inserts = []
        self._clicks = []
        self.snapshot_canned = []
        self.find_canned = [
            {"ref": "eid:fnd_btn", "tag": "button", "role": "button",
             "name": "立即注册", "in_viewport": True},
            {"ref": "eid:fnd_lnk", "tag": "a", "role": "link",
             "name": "注册即送优惠券", "in_viewport": False},
        ]
        self._centers = {
            "eid:fnd_btn": [150, 200],
            "eid:fnd_lnk": [40, 90],
        }

    def _send(self, method, params=None):
        if method == "Page.captureScreenshot":
            import base64
            import io
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGB", (200, 120), (255, 255, 255)).save(buf, "PNG")
            return {"data": base64.b64encode(buf.getvalue()).decode("ascii")}
        if method == "Input.insertText":
            self._inserts.append(params or {})
        return {}

    def mouse_click(self, x, y, button="left", click_count=1):
        self._clicks.append((x, y, button))
        return {"clicked": True, "x": x, "y": y, "button": button}

    def _center_for(self, expr):
        for ref, center in self._centers.items():
            if ref in str(expr):
                return center
        return None

    def _evaluate(self, expression):
        expr = str(expression).strip()
        if expr == "window.devicePixelRatio":
            return self.dpr
        if "location.href" in expr and "innerHTML.length" not in expr:
            return self.url
        if "document.title" in expr:
            return "页面"
        if expr == _SNAPSHOT_JS.strip():
            return [dict(x) for x in self.snapshot_canned]
        if expr == _INSPECT_JS.strip():
            return [dict(x) for x in self.find_canned]
        if "const wantRole" in expr:
            return [dict(x) for x in self.find_canned]
        if "innerHTML.length" in expr:
            return self.dom_len
        if expr.startswith("!!document.querySelector"):
            return self._center_for(expr) is not None
        if "data-miniyu-ref" in expr and "getBoundingClientRect" in expr:
            return self._center_for(expr)
        if "data-miniyu-ref" in expr and "focus" in expr:
            return self._center_for(expr) is not None
        return None


class TestHandleBasics(unittest.TestCase):
    def test_find_allocates_unique_handles(self):
        fb = FakeBrowser()
        hits = fb.find(text="注册")
        self.assertEqual(len(hits), 2)
        handles = [h["handle"] for h in hits]
        self.assertTrue(all(h.startswith("e") for h in handles))
        self.assertEqual(len(handles), len(set(handles)))   # 互不重复

    def test_two_same_name_elements_handles_unique(self):
        fb = FakeBrowser()
        fb.find_canned = [
            {"ref": "eid:a", "tag": "button", "role": "button", "name": "同", "in_viewport": True},
            {"ref": "eid:b", "tag": "button", "role": "button", "name": "同", "in_viewport": True},
        ]
        fb._centers = {"eid:a": [1, 2], "eid:b": [3, 4]}
        hits = fb.find(role="button")
        self.assertEqual(len(hits), 2)
        self.assertNotEqual(hits[0]["handle"], hits[1]["handle"])

    def test_snapshot_allocates_handles(self):
        fb = FakeBrowser()
        fb.snapshot_canned = [
            {"ref": "ebody:0_div:0", "tag": "button", "role": "button", "name": "豆包",
             "editable": False},
        ]
        items = fb.snapshot()
        self.assertEqual(len(items), 1)
        self.assertIn("handle", items[0])
        self.assertTrue(items[0]["handle"].startswith("e"))

    def test_inspect_elements_include_handle(self):
        fb = FakeBrowser()
        r = fb.annotated_screenshot(output=None)
        self.assertGreaterEqual(len(r["elements"]), 1)
        for e in r["elements"]:
            self.assertIn("handle", e)
            self.assertIn("ref", e)


class TestHandleResolution(unittest.TestCase):
    def test_click_by_handle_resolves_internal_ref(self):
        fb = FakeBrowser()
        h = fb.find(text="注册")[0]["handle"]
        ret = fb.click(target=h)
        self.assertTrue(ret["clicked"])
        self.assertEqual(ret["ref"], "eid:fnd_btn")   # handle → 内部 ref
        self.assertEqual(fb._clicks[-1][:2], (150, 200))

    def test_type_by_handle_succeeds(self):
        fb = FakeBrowser()
        h = fb.find(text="注册")[0]["handle"]
        ret = fb.type_text("你好", target=h)
        self.assertEqual(ret["ref"], "eid:fnd_btn")
        self.assertEqual(fb._inserts[-1]["text"], "你好")

    def test_stale_handle_after_page_change_raises(self):
        fb = FakeBrowser()
        h = fb.find(text="注册")[0]["handle"]
        fb.url = "https://example.com/elsewhere"      # 页面导航 → session 失效
        with self.assertRaises(SoMStaleError):
            fb.click(target=h)

    def test_new_session_reinumbers_not_colliding(self):
        fb = FakeBrowser()
        old = [x["handle"] for x in fb.find(text="注册")]
        fb.url = "https://example.com/other"          # 页面变化 → 新 session
        fb.find_canned = [
            {"ref": "eid:nav", "tag": "a", "role": "link", "name": "登录", "in_viewport": True},
        ]
        fb._centers = {"eid:nav": [5, 6]}
        new = [x["handle"] for x in fb.find(role="link")]
        self.assertFalse(set(old) & set(new))         # 新 handle 不与旧 handle 冲突


class TestLongRefRejection(unittest.TestCase):
    def test_is_long_internal_ref_detection(self):
        self.assertTrue(is_ref(LONG_REF))
        self.assertTrue(is_long_internal_ref(LONG_REF))
        self.assertFalse(is_long_internal_ref("e17"))

    def test_short_semantic_ref_not_rejected(self):
        self.assertTrue(is_ref(SHORT_REF))
        self.assertFalse(is_long_internal_ref(SHORT_REF))
        self.assertFalse(is_long_internal_ref("som:3"))
        self.assertFalse(is_long_internal_ref("e3"))

    def test_tool_type_rejects_long_internal_ref(self):
        r = ToolRegistry()
        with self.assertRaises(LookupError):
            r.browser_type("你好", target=LONG_REF)
        with self.assertRaises(LookupError):
            r.browser_click(target=LONG_REF)


if __name__ == "__main__":
    unittest.main(verbosity=2)