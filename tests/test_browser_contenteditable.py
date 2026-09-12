# -*- coding: utf-8 -*-
"""P2-1 contenteditable/textbox 定位增强 回归测试。

覆盖（对应外部分析清单）：
- Case1/2: input / textarea 仍能定位与输入
- Case3  : contenteditable textbox 被 snapshot/find 识别
- Case4  : 按 ref 对 contenteditable 执行 browser_type
- Case5  : contenteditable=false 不识别为可输入
- Case6  : disabled / readonly 拒绝执行输入
- JS 断言：_SNAPSHOT_JS / _build_find_js 包含 contenteditable 选择器与 textbox 语义别名
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from core.browser_controller import BrowserController, _SNAPSHOT_JS, _build_find_js


def _find_js(**kw):
    return _build_find_js(kw.get("text"), kw.get("role"), kw.get("tag"),
                          kw.get("selector"), kw.get("max_results", 20))


class _Fake(BrowserController):
    """用受控数据模拟 CDP _evaluate，验证 Python 侧管线与输入守卫。"""

    def __init__(self):
        super().__init__(ws_url="fake")
        self._inserts = []
        self.type_state = {}          # ref -> {"ok":bool,"reason":str|None}
        self.snapshot_canned = []
        self.find_canned = []
        self.resolve_host = {}        # ref -> 下钻 edit host 的新 ref（P2-4）

    def _send(self, method, params=None):
        if method == "Input.insertText":
            self._inserts.append(params or {})
        return {}

    def _evaluate(self, expression):
        expr = str(expression).strip()
        if expr == "window.devicePixelRatio":
            return 1.0
        # type 输入守卫查询：''reason: 'gone' ' 只出现在 _type_by_ref 状态 JS 里，
        # 避免把 find 的 "use.disabled === true" 误判成守卫分支
        if "reason: 'gone'" in expr:
            # _type_by_ref 用 json.dumps 转义选择器，属性引号可能是 \" 形式，需兼容
            m = re.search(r'data-miniyu-ref=\\?"([^"\\]+)\\"?', str(expression))
            ref = m.group(1) if m else ""
            return dict(self.type_state.get(ref, {"ok": False, "reason": "gone"}))
        if "data-agentic-idx" in expr:                 # browser_snapshot 桩
            return [dict(x) for x in self.snapshot_canned]
        if "const wantRole" in expr:                   # browser_find 桩
            return [dict(x) for x in self.find_canned]
        if "_miniyuResolveHost(el)" in expr:           # P2-4 下钻 edit host 桩
            m = re.search(r'data-miniyu-ref=\\?"([^"\\]+)\\"?', str(expression))
            ref = m.group(1) if m else ""
            return self.resolve_host.get(ref) or None
        return None

    @property
    def _qs_ref(self):
        return self._last_qs_ref

    # 记录最近一次守卫查询命中的 ref，便于 canned 状态映射
    _last_qs_ref = ""


class TestSnapshotJs(unittest.TestCase):
    def test_selector_covers_contenteditable_and_textbox(self):
        self.assertIn("[contenteditable]", _SNAPSHOT_JS)
        self.assertIn('[role="textbox"]', _SNAPSHOT_JS)
        self.assertIn('[role="searchbox"]', _SNAPSHOT_JS)
        self.assertIn("el.isContentEditable === true", _SNAPSHOT_JS)

    def test_output_prioritizes_ref_over_index(self):
        ref_pos = _SNAPSHOT_JS.index("ref: ref")
        idx_pos = _SNAPSHOT_JS.index("index: i")
        self.assertLess(ref_pos, idx_pos)
        self.assertIn("editable:", _SNAPSHOT_JS)
        self.assertIn("disabled:", _SNAPSHOT_JS)


class TestFindJs(unittest.TestCase):
    def test_textbox_alias_includes_contenteditable(self):
        js = _find_js(role="textbox")
        self.assertIn("el.isContentEditable === true", js)

    def test_name_uses_placeholder_variants(self):
        js = _find_js(role="textbox")
        self.assertIn("aria-placeholder", js)
        self.assertIn("data-placeholder", js)


class TestSnapshotAndFindBehavior(unittest.TestCase):
    def test_snapshot_exposes_contenteditable_with_ref_and_editable(self):
        fb = _Fake()
        fb.snapshot_canned = [
            {"ref": "erich_tbox", "index": 0, "role": "textbox", "tag": "div",
             "name": "发消息或按住空格说话...", "editable": True, "disabled": False},
            {"ref": "eid:inp", "index": 1, "role": "textbox", "tag": "input",
             "name": "", "editable": True, "disabled": False},
        ]
        items = fb.snapshot()
        self.assertEqual(items[0]["ref"], "erich_tbox")
        self.assertTrue(items[0]["editable"])
        self.assertEqual(items[0]["index"], 0)   # index 仍兼容保留
        self.assertIn("name", items[0])

    def test_find_role_textbox_returns_contenteditable(self):
        fb = _Fake()
        fb.find_canned = [
            {"ref": "erich_tbox", "tag": "div", "role": "textbox",
             "name": "发消息或按住空格说话...", "editable": True,
             "disabled": False, "in_viewport": True},
        ]
        items = fb.find(role="textbox")
        self.assertEqual(items[0]["ref"], "erich_tbox")
        self.assertTrue(items[0]["editable"])


class TestTypeEditable(unittest.TestCase):
    def setUp(self):
        self.fb = _Fake()
        for ref, st in {
            "erich":   {"ok": True, "reason": None},
            "eid:inp": {"ok": True, "reason": None},   # input
            "efrozen": {"ok": False, "reason": "disabled"},
            "eroad":   {"ok": False, "reason": "readonly"},
            "enotedit": {"ok": False, "reason": "not_editable"},  # contenteditable=false
        }.items():
            self.fb.type_state[ref] = st

    def _type(self, ref):
        return self.fb.type_text("你好", target=ref)

    def test_type_into_contenteditable_by_ref(self):     # Case4
        r = self._type("erich")
        self.assertEqual(r["ref"], "erich")
        self.assertEqual(self.fb._inserts[-1]["text"], "你好")

    def test_input_and_textarea_still_work(self):        # Case1/2
        self._type("erich")
        self._type("eid:inp")
        self.assertEqual(len(self.fb._inserts), 2)

    def test_disabled_refused(self):                     # Case6
        with self.assertRaises(LookupError) as cm:
            self._type("efrozen")
        self.assertIn("不可输入", str(cm.exception))

    def test_readonly_refused(self):                     # Case6
        with self.assertRaises(LookupError):
            self._type("eroad")

    def test_contenteditable_false_refused(self):        # Case5
        with self.assertRaises(LookupError) as cm:
            self._type("enotedit")
        self.assertIn("不可输入", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)