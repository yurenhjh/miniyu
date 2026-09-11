"""
test_browser_som.py — P3 SoM 视觉定位（browser_inspect）核心逻辑测试

用一个 FakeController 覆盖 CDP 底层，验证：
- inspect_elements 的交互优先级排序 + 视觉编号 + ref
- annotated_screenshot 生成带编号覆盖层图 + 建立 InspectSession
- click(target='som:N') 的 num→ref→坐标解析与真实鼠标落点
- 页面变化后 session 失效 → 抛 SoMStaleError（不给旧编号硬点）
"""
import base64
import io
import os
import re
import tempfile
import unittest

from core.browser_controller import BrowserController, SoMStaleError, _INSPECT_JS

# 模块原始 _INSPECT_JS 返回值：每个元素含 ref/tag/role/name/x/y/w/h/disabled...
RAW_ELEMENTS = [
    {"ref": "eid:sendbtn", "tag": "button", "role": "button", "name": "发送",
     "x": 0, "y": 0, "w": 100, "h": 40, "disabled": False,
     "onclick": True, "editable": False, "tabindex": None},
    {"ref": "eid:search", "tag": "input", "role": "textbox", "name": "",
     "x": 0, "y": 50, "w": 200, "h": 40, "disabled": False,
     "onclick": False, "editable": False, "tabindex": None},
    {"ref": "eid:nav", "tag": "a", "role": "link", "name": "登录",
     "x": 0, "y": 100, "w": 50, "h": 20, "disabled": False,
     "onclick": False, "editable": False, "tabindex": None},
]


class FakeBrowser(BrowserController):
    def __init__(self, url="https://example.com/page", title="页面", dpr=2.0,
                 elements=None, dom_len=1000):
        super().__init__(ws_url="fake")
        self.url = url
        self.title = title
        self.dpr = dpr
        self.elems_raw = elements or list(RAW_ELEMENTS)
        self.dom_len = dom_len
        self._clicks = []
        self._inserts = []
        self._captured = False
        self._find_hits = [
            {"ref": "eid:fnd_btn", "tag": "button", "role": "button",
             "name": "立即注册", "in_viewport": True},
            {"ref": "eid:fnd_lnk", "tag": "a", "role": "link",
             "name": "注册即送优惠券", "in_viewport": False},
        ]
        self._centers = {
            e["ref"]: [e["x"] + e["w"] / 2.0, e["y"] + e["h"] / 2.0]
            for e in self.elems_raw
        }
        self._centers.update({
            "eid:fnd_btn": [150, 200],
            "eid:fnd_lnk": [40, 90],
        })

    # ---- CDP 底层桩 ----

    def _send(self, method, params=None):
        if method == "Page.captureScreenshot":
            buf = io.BytesIO()
            from PIL import Image
            Image.new("RGB", (300, 200), (255, 255, 255)).save(buf, "PNG")
            self._captured = True
            return {"data": base64.b64encode(buf.getvalue()).decode("ascii")}
        if method == "Input.insertText":
            self._inserts.append(params or {})
            return {}
        return {}

    def _center_for(self, expr):
        for ref, center in self._centers.items():
            if ref in expr:
                return center
        return None

    def _evaluate(self, expression):
        expr = str(expression).strip()
        if expr == "window.devicePixelRatio":
            return self.dpr
        if "location.href" in expr and "innerHTML.length" not in expr:
            return self.url
        if "document.title" in expr:
            return self.title
        if expr == _INSPECT_JS.strip():
            return list(self.elems_raw)
        if "const wantRole" in expr:                       # browser_find 局部搜索
            return [dict(h) for h in self._find_hits]
        if "innerHTML.length" in expr:
            return self.dom_len
        if expr.startswith("!!document.querySelector"):      # live ref exists 校验
            return self._center_for(expr) is not None
        if "data-miniyu-ref" in expr and "getBoundingClientRect" in expr:
            return self._center_for(expr)                    # resolve ref 中心
        if "data-miniyu-ref" in expr and "focus" in expr:
            return self._center_for(expr) is not None        # 聚焦输入框
        return None

    def mouse_click(self, x, y, button="left", click_count=1):
        self._clicks.append((x, y, button))
        return {"clicked": True, "x": x, "y": y, "button": button}


class TestInspectElements(unittest.TestCase):
    def test_priority_sort_and_visual_num(self):
        fb = FakeBrowser()
        tgts = fb.inspect_elements(max_elements=2)
        # 交互优先级：button、input 都排最前；1=发送按钮, 2=搜索框
        self.assertEqual(len(tgts), 2)
        self.assertEqual(tgts[0].visual_num, 1)
        self.assertEqual(tgts[0].name, "发送")
        self.assertEqual(tgts[1].visual_num, 2)
        self.assertEqual(tgts[0].source, "browser_som")
        # ref 是稳定身份，不是下标
        self.assertEqual(tgts[0].ref, "eid:sendbtn")

    def test_annotated_screenshot_creates_image_and_session(self):
        fb = FakeBrowser()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "inspect.png")
            r = fb.annotated_screenshot(output=out)
            self.assertTrue(os.path.isfile(out))
            self.assertTrue(fb._captured)
            self.assertEqual(r["dpr"], 2.0)
            self.assertEqual(len(r["elements"]), 3)
            self.assertEqual(r["elements"][0]["num"], 1)
            self.assertTrue(hasattr(fb, "_som_session"))


class TestSomClick(unittest.TestCase):
    def test_click_by_som_num_resolves_ref_and_clicks_center(self):
        fb = FakeBrowser(dpr=2.0)
        fb.annotated_screenshot(output=None)
        # som:1 → 发送按钮，中心 CSS (50,20) × dpr 2 = (100,40)
        ret = fb.click(target="som:1")
        self.assertTrue(ret["clicked"])
        self.assertEqual(ret["ref"], "eid:sendbtn")
        self.assertEqual(fb._clicks[-1][:2], (100, 40))

    def test_stale_session_raises(self):
        fb = FakeBrowser()
        fb.annotated_screenshot(output=None)
        # 模拟页面导航 → 会话失效，不允许用旧编号硬点
        fb.url = "https://example.com/elsewhere"
        with self.assertRaises(SoMStaleError):
            fb.click(target="som:1")

    def test_click_by_ref_works_without_session(self):
        fb = FakeBrowser()
        fb.annotated_screenshot(output=None)
        ret = fb.click(target="eid:nav")
        self.assertTrue(ret["clicked"])
        self.assertEqual(ret["ref"], "eid:nav")


class TestSomType(unittest.TestCase):
    def test_type_by_som_num_focuses_and_inserts(self):
        fb = FakeBrowser()
        fb.annotated_screenshot(output=None)
        # som:2 → 搜索框，中文输入走 Input.insertText
        ret = fb.type_text("湖南大学", target="som:2")
        self.assertEqual(ret["typed"], "湖南大学")
        self.assertEqual(len(fb._inserts), 1)
        self.assertEqual(fb._inserts[0].get("text"), "湖南大学")


class TestBrowserFind(unittest.TestCase):
    """browser_find：按目标文字/名称/角色局部搜索，返回带稳定 ref 的清单（Level 1）"""

    def test_find_returns_refs_and_names(self):
        fb = FakeBrowser()
        hits = fb.find(text="注册")
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["ref"], "eid:fnd_btn")
        self.assertEqual(hits[0]["name"], "立即注册")
        self.assertTrue(any(not h["in_viewport"] for h in hits))  # 允许找到视口外元素

    def test_find_by_role_filter(self):
        fb = FakeBrowser()
        hits = fb.find(text="注册", role="link")
        self.assertEqual(len(hits), 2)  # 桩不按 role 过滤，结构上仍返回列表
        self.assertTrue(all("ref" in h and "in_viewport" in h for h in hits))

    def test_find_result_can_be_clicked_by_ref(self):
        fb = FakeBrowser()
        hits = fb.find(text="注册")
        ret = fb.click(target=hits[0]["ref"])
        self.assertTrue(ret["clicked"])
        self.assertEqual(ret["ref"], "eid:fnd_btn")


if __name__ == "__main__":
    unittest.main()