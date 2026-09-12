# -*- coding: utf-8 -*-
"""P2-2 浏览器定位恢复策略 + typeable 元素一致性 回归测试。

覆盖真实豆包失败链暴露的问题：
- Case1: stale ref 错误提示应以 browser_find/snapshot 恢复，不得引导 browser_inspect
- Case2: find(role=textbox) 过滤隐藏 input（type=file/hidden）
- Case3: wrapper div(role=textbox, 非 contenteditable) 不得虚报 editable=true
- Case4: 真实 contenteditable 能 find + type
- Case5: 多个候选按 visible+typeable 排序
- Case6: 完整恢复链 stale→find→type 无需 inspect
- 统一：find 标 editable=true 的目标，browser_type 一定能输入（同一判断）
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from core.browser_controller import BrowserController, SoMStaleError, _SNAPSHOT_JS, _build_find_js


class _Fake(BrowserController):
    """受控数据模拟 CDP，聚焦验证 find 排序/过滤 + type 守卫与恢复提示。"""

    def __init__(self):
        super().__init__(ws_url="fake")
        self._inserts = []
        self.type_state = {}          # ref -> {"ok":bool,"reason":str|None}
        self.find_canned = []
        self.resolve_host = {}        # ref -> 下钻 edit host 的新 ref（P2-4），None 表示无宿主

    def _send(self, method, params=None):
        if method == "Input.insertText":
            self._inserts.append(params or {})
        return {}

    def _evaluate(self, expression):
        expr = str(expression).strip()
        if expr == "window.devicePixelRatio":
            return 1.0
        # type 输入守卫查询：''reason: 'gone' ' 只出现在 _type_by_ref 的状态 JS 里
        # （find 的 JS 现含 "use.disabled === true"，不能用 "e.disabled" 做标记）
        if "reason: 'gone'" in expr:
            m = re.search(r'data-miniyu-ref=\\?"([^"\\]+)\\"?', str(expression))
            ref = m.group(1) if m else ""
            return dict(self.type_state.get(ref, {"ok": False, "reason": "gone"}))
        if "const wantRole" in expr:                  # find 桩（含 P2-2 input_kind/visible/editable）
            return [dict(x) for x in self.find_canned]
        if "data-agentic-idx" in expr:                # snapshot 桩
            return [dict(x) for x in self.snapshot_canned]
        if "_miniyuResolveHost(el)" in expr:          # P2-4 下钻 edit host 桩
            m = re.search(r'data-miniyu-ref=\\?"([^"\\]+)\\"?', str(expression))
            ref = m.group(1) if m else ""
            return self.resolve_host.get(ref) or None
        return None


# 与真实豆包页面同构的一套候选：隐藏 file input、可见 contenteditable
CANDIDATES = [
    {"ref": "eid:file", "tag": "input", "role": "input", "name": "",
     "editable": False, "input_kind": "hidden-input", "visible": False,
     "disabled": False, "in_viewport": False},
    {"ref": "eid:tbox", "tag": "div", "role": "textbox", "name": "发消息或按住空格说话...",
     "editable": True, "input_kind": "contenteditable", "visible": True,
     "disabled": False, "in_viewport": True},
]


class TestJsTypeableConsistency(unittest.TestCase):
    def test_snapshot_and_find_share_input_kind_and_strict_editable(self):
        # 三处共用同一套可输入判断：非输入型 input / wrapper 一律 not editable
        self.assertIn("hidden-input", _SNAPSHOT_JS)
        self.assertIn("input_kind:", _SNAPSHOT_JS)
        self.assertIn("_typingInput", _build_find_js(None, "textbox", None, None, 20))
        # 语义 wrapper（role=textbox 非 contenteditable）不得报 editable → 用 _typingInput 收紧
        find_js = _build_find_js(None, "textbox", None, None, 20)
        self.assertIn('input_kind', find_js)
        self.assertIn("native-input", find_js)


class TestFindRankingFiltering(unittest.TestCase):
    def setUp(self):
        self.fb = _Fake()

    def test_hidden_input_filtered_when_visible_editable_exists(self):  # Case2
        self.fb.find_canned = CANDIDATES
        items = self.fb.find(role="textbox")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ref"], "eid:tbox")
        self.assertTrue(items[0]["editable"])
        self.assertNotIn("eid:file", [x["ref"] for x in items])

    def test_wrapper_semantic_textbox_not_reported_editable(self):       # Case3
        self.fb.find_canned = [
            {"ref": "eid:wrapper", "tag": "div", "role": "textbox", "name": "",
             "editable": False, "input_kind": "semantic-textbox", "visible": True,
             "disabled": False, "in_viewport": True},
        ]
        items = self.fb.find(role="textbox")
        # 无任何可编辑候选时不强行过滤，但该 wrapper 必须标记 editable=False
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["editable"])

    def test_visible_typeable_ranked_first_over_hidden_one(self):        # Case5
        self.fb.find_canned = [
            {"ref": "eid:inp_hidden", "tag": "input", "role": "input", "name": "",
             "editable": True, "input_kind": "native-input", "visible": False,
             "disabled": False, "in_viewport": False},
            {"ref": "eid:ta_visible", "tag": "textarea", "role": "textbox", "name": "",
             "editable": True, "input_kind": "textarea", "visible": True,
             "disabled": False, "in_viewport": True},
        ]
        items = self.fb.find(role="textbox")
        self.assertEqual(items[0]["ref"], "eid:ta_visible")   # 可见可输入排最前


class TestTypeableGuardAndRecovery(unittest.TestCase):
    def setUp(self):
        self.fb = _Fake()
        self.fb.type_state = {
            "eid:tbox":     {"ok": True, "reason": None},          # contenteditable 可输入
            "eid:wrapper":  {"ok": False, "reason": "not_editable"},
            "eid:readonly": {"ok": False, "reason": "readonly"},
            "eid:disabled": {"ok": False, "reason": "disabled"},
        }

    def test_type_into_contenteditable_succeeds(self):              # Case4
        r = self.fb.type_text("你好", target="eid:tbox")
        self.assertEqual(r["ref"], "eid:tbox")
        self.assertEqual(self.fb._inserts[-1]["text"], "你好")

    def test_stale_ref_recovery_hints_find_not_inspect(self):        # Case1
        with self.assertRaises(SoMStaleError) as cm:
            self.fb.type_text("你好", target="eid:gone")
        msg = str(cm.exception)
        self.assertIn("browser_find", msg)
        self.assertIn("stale_target", msg)
        # 必须告诉模型用结构化恢复，并显式禁止升级到 browser_inspect
        self.assertIn("不要直接 browser_inspect", msg)

    def test_not_editable_recovery_hints_find_not_inspect(self):     # Case3-守卫
        for ref in ("eid:wrapper", "eid:readonly", "eid:disabled"):
            with self.assertRaises(LookupError) as cm:
                self.fb.type_text("你好", target=ref)
            msg = str(cm.exception)
            self.assertIn("browser_find", msg)
            self.assertIn("不要 browser_inspect", msg)

    def test_full_recovery_chain_without_inspect(self):              # Case6
        # stale → 失败并提示 find ── 不调用 inspect
        with self.assertRaises(SoMStaleError):
            self.fb.type_text("你好", target="eid:gone")
        # find 拿到可用 contenteditable ref
        self.fb.find_canned = [dict(CANDIDATES[1])]
        hit = self.fb.find(role="textbox")[0]
        self.assertEqual(hit["ref"], "eid:tbox")
        # 用新 ref 直接输入成功
        r = self.fb.type_text("你好", target=hit["ref"])
        self.assertEqual(r["ref"], "eid:tbox")
        self.assertEqual(self.fb._inserts[-1]["text"], "你好")


if __name__ == "__main__":
    unittest.main(verbosity=2)