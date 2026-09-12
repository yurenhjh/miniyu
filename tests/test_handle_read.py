# -*- coding: utf-8 -*-
"""test_handle_read.py — P2-7 A1 修复：browser_read_text 消费 Target Handle

覆盖：
- read_text(target=<handle>) 能把短 handle 解析到内部 ref 并读取元素文本
- read_text(target=<猜测的 CSS selector>) 被拒绝（契约：find→handle→read 闭环）
- read_text(selector=...) / 整页读取保持向后兼容
- stale handle（页面变化后）读取 → 提示重新 browser_find
- 工具层 browser_read_text 透传 target
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.browser_controller import BrowserController, _SNAPSHOT_JS, _INSPECT_JS
from core.tool_registry import ToolRegistry


class ReadFakeBrowser(BrowserController):
    """受控 mock：find 生成 handle，read_text 按 ref/selector 返回罐装文本。"""

    def __init__(self, url="https://example.com/page", dom_len=1000):
        super().__init__(ws_url="fake")
        self.url = url
        self.dom_len = dom_len
        self.find_canned = [
            {"ref": "eid:btn", "tag": "button", "role": "button",
             "name": "查看更多", "in_viewport": True},
            {"ref": "eid:para2", "tag": "p", "role": "text",
             "name": "第二段", "in_viewport": True},
        ]
        self.text_by_ref = {
            "eid:btn": "查看更多",
            "eid:para2": "第二段（展开后可见）：本段用于验证定向读取。",
        }

    def _read_page_sig(self):
        # 简化指纹：只看 url + dom 长度，便于导航后模拟失效
        return (self.url, self.dom_len)

    def _evaluate(self, expression):
        expr = str(expression).strip()
        if expr == "window.devicePixelRatio":
            return 1.0
        if "location.href" in expr and "innerHTML.length" not in expr:
            return self.url
        if "document.title" in expr:
            return "页面"
        if expr == _SNAPSHOT_JS.strip():
            return [dict(x) for x in self.find_canned]
        if expr == _INSPECT_JS.strip():
            return [dict(x) for x in self.find_canned]
        if "const wantRole" in expr:
            # 真实 find 会按文字过滤；从 JS 的 query 行(json 编码，可能含 \u 转义)解码搜索词再过滤，
            # 避免 [0] 落在非目标项。
            items = [dict(x) for x in self.find_canned]
            m = re.search(r'const query = ("(?:\\.|[^"\\])*");', expr)
            if m:
                try:
                    q = json.loads(m.group(1))
                except Exception:
                    q = ""
                if q:
                    items = [x for x in items if q in x.get("name", "")]
            return items
        if "innerHTML.length" in expr:
            return self.dom_len
        # read_text(target=handle)：按 data-miniyu-ref 读元素文本
        if "data-miniyu-ref" in expr and "innerText" in expr:
            for ref, txt in self.text_by_ref.items():
                if ref in expr:
                    return txt
            return None
        # read_text(selector=...)：CSS 选择器读元素文本
        if "document.querySelector" in expr and "innerText" in expr:
            return "selector-text"
        # 整页读取
        if "document.body ? document.body.innerText" in expr:
            return "whole-body-text"
        return None


class TestReadByHandle(unittest.TestCase):

    def test_read_by_handle_reads_element_text(self):
        fb = ReadFakeBrowser()
        h = fb.find(text="第二段")[0]["handle"]
        # 契约：find→handle→read 闭环，读出该元素定向文本
        self.assertEqual(fb.read_text(target=h), "第二段（展开后可见）：本段用于验证定向读取。")
        # 同样 handle 可为 click/type 复用
        other = fb.find(text="查看更多")[0]["handle"]
        self.assertEqual(fb.read_text(target=other), "查看更多")

    def test_read_rejects_guessed_css_selector_as_target(self):
        fb = ReadFakeBrowser()
        # 上一轮 A1 失败形态：自猜 #section1 当 target → 必须被拒绝并给引导
        with self.assertRaises(LookupError) as cm:
            fb.read_text(target="#section1")
        msg = str(cm.exception)
        self.assertIn("不是有效 Target Handle", msg)
        self.assertIn("browser_find", msg)
        self.assertIn("selector=", msg)

    def test_read_by_selector_kept_backward_compat(self):
        fb = ReadFakeBrowser()
        self.assertEqual(fb.read_text(selector=".x"), "selector-text")

    def test_read_whole_page_kept_backward_compat(self):
        fb = ReadFakeBrowser()
        self.assertEqual(fb.read_text(), "whole-body-text")

    def test_stale_handle_read_prompts_refind(self):
        fb = ReadFakeBrowser()
        h = fb.find(text="第二段")[0]["handle"]
        fb.url = "https://example.com/elsewhere"   # 页面导航 → handle 失效
        with self.assertRaises(LookupError) as cm:
            fb.read_text(target=h)
        self.assertIn("重新 browser_find", str(cm.exception))

    def test_tool_layer_passthrough_target(self):
        tb = ReadFakeBrowser()
        h = tb.find(text="第二段")[0]["handle"]
        r = ToolRegistry()
        r._browser = tb   # 注入受控 controller，走 tool 层 browser_read_text 透传
        self.assertEqual(r.browser_read_text(target=h),
                         "第二段（展开后可见）：本段用于验证定向读取。")


if __name__ == "__main__":
    unittest.main(verbosity=2)