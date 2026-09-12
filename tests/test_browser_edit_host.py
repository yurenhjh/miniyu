# -*- coding: utf-8 -*-
"""P2-4 浏览器编辑宿主（edit host）解析 回归测试。

核心不变式：browser_find 找到的 semantic textbox 可能是不可直接输入的 wrapper，
真正的编辑宿主在其内部子节点；handle/ref 必须收敛到真实宿主，browser_type 内部
再做一次确定性 recovery，避免把这种确定性浏览器问题抛回 LLM 消耗 5~20 个回合。

两层测试：
  A) JS 层：用 Node 直接运行真实注入的 _EDIT_HOST_HELPERS_JS（单一事实来源），
     用极简假 DOM 覆盖 11 个 primitive 解析场景（优先级/排除/重渲染/自身即宿主）。
  B) Python 层：_type_ref_with_recovery 工具内确定性 recovery + run_log 一致性去重。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from core.browser_controller import BrowserController, _EDIT_HOST_HELPERS_JS
from tests.test_browser_typeable_recovery import _Fake


# =====================================================================
# A. JS 层：Node 运行真实_helper，极简假 DOM
# =====================================================================

_NODE_CASES = r"""
const _global = globalThis;
if (typeof _global.getComputedStyle !== 'function') {
  _global.getComputedStyle = (el) => ({ display: el._display, visibility: el._visibility });
}
const mk = (tag, attrs = {}, opts = {}) => {
  const node = {
    nodeType: 1,
    _tag: tag,
    _mkname: opts.mkname || (tag + '_' + Math.random().toString(36).slice(2, 6)),
    tagName: tag.toUpperCase(),
    children: [],
    parentElement: null,
    id: opts.id || '',
    _attrs: { ...attrs },
    isContentEditable: attrs.contenteditable === 'true',
    disabled: !!opts.disabled,
    readOnly: !!opts.readonly,
    _display: opts.display !== undefined ? opts.display : 'block',
    _visibility: opts.visibility !== undefined ? opts.visibility : 'visible',
    _w: opts.w !== undefined ? opts.w : 100,
    _h: opts.h !== undefined ? opts.h : 30,
    getAttribute(name) { return this._attrs[name] !== undefined ? this._attrs[name] : null; },
    getBoundingClientRect() { return { width: this._w, height: this._h }; },
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; },
    querySelectorAll(sel) {
      const out = [];
      const tokens = sel.split(',').map((s) => s.trim());
      const walk = (n) => {
        for (const c of n.children || []) {
          const m = tokens.some((t) => {
            if (t === 'textarea') return c.tagName === 'TEXTAREA';
            if (t === 'input') return c.tagName === 'INPUT';
            if (t === 'div') return c.tagName === 'DIV';
            if (t === '[contenteditable]') return c.getAttribute('contenteditable') !== null;
            if (t === '[contenteditable="true"]') return c.getAttribute('contenteditable') === 'true';
            return false;
          });
          if (m) out.push(c);
          walk(c);
        }
      };
      walk(this);
      return out;
    },
  };
  (opts.children || []).forEach((c) => { node.children.push(c); c.parentElement = node; });
  return node;
};

const results = [];
const check = (label, got, expectName) => {
  const ok = (expectName === null) ? (got === null)
             : (!!got && got._mkname === expectName);
  results.push({ label, ok, got: got ? got.tagName + ':' + got._mkname : 'null',
                 expect: String(expectName) });
};

// 1. wrapper -> textarea
let ta = mk('textarea', {}, { mkname: 'TA' });
let w = mk('div', { role: 'textbox' }, { children: [ta] });
check('wrapper->textarea', _miniyuResolveHost(w), 'TA');

// 2. wrapper -> input[type=text]
let inp = mk('input', { type: 'text' }, { mkname: 'IN' });
w = mk('div', { role: 'textbox' }, { children: [inp] });
check('wrapper->input', _miniyuResolveHost(w), 'IN');

// 3. wrapper -> contenteditable child
let ce = mk('div', { contenteditable: 'true' }, { mkname: 'CE' });
w = mk('div', { role: 'textbox' }, { children: [ce] });
check('wrapper->contenteditable_child', _miniyuResolveHost(w), 'CE');

// 4. element 自身就是 textarea
ta = mk('textarea', {}, { mkname: 'TA_SELF' });
check('self->textarea', _miniyuResolveHost(ta), 'TA_SELF');

// 5. element 自身就是 input
inp = mk('input', { type: 'email' }, { mkname: 'IN_SELF' });
check('self->input', _miniyuResolveHost(inp), 'IN_SELF');

// 6. element 自身就是 contenteditable
ce = mk('div', { contenteditable: 'true' }, { mkname: 'CE_SELF' });
check('self->contenteditable', _miniyuResolveHost(ce), 'CE_SELF');

// 7. hidden input 不得被选中：wrapper 内 hidden + 可见 contenteditable
let hidden = mk('input', { type: 'hidden' }, { mkname: 'HID' });
ce = mk('div', { contenteditable: 'true' }, { mkname: 'CE_VIS' });
w = mk('div', { role: 'textbox' }, { children: [hidden, ce] });
check('hidden_input_skipped', _miniyuResolveHost(w), 'CE_VIS');
// 仅 hidden input -> 无宿主
w = mk('div', { role: 'textbox' }, { children: [hidden] });
check('only_hidden_input_none', _miniyuResolveHost(w), null);

// 8. readonly input 不得被选中
let ro = mk('input', { type: 'text' }, { readonly: true, mkname: 'RO' });
ce = mk('div', { contenteditable: 'true' }, { mkname: 'CE_AFTER_RO' });
w = mk('div', { role: 'textbox' }, { children: [ro, ce] });
check('readonly_skipped', _miniyuResolveHost(w), 'CE_AFTER_RO');
w = mk('div', { role: 'textbox' }, { children: [ro] });
check('only_readonly_none', _miniyuResolveHost(w), null);

// 9. disabled input 不得被选中
let dis = mk('input', { type: 'text' }, { disabled: true, mkname: 'DIS' });
ce = mk('div', { contenteditable: 'true' }, { mkname: 'CE_AFTER_DIS' });
w = mk('div', { role: 'textbox' }, { children: [dis, ce] });
check('disabled_skipped', _miniyuResolveHost(w), 'CE_AFTER_DIS');
w = mk('div', { role: 'textbox' }, { children: [dis] });
check('only_disabled_none', _miniyuResolveHost(w), null);

// 10. 多个候选：固定优先级 textarea > input > contenteditable，不看 document 顺序
let ceFirst = mk('div', { contenteditable: 'true' }, { mkname: 'CE_FIRST' });
let taSecond = mk('textarea', {}, { mkname: 'TA_SECOND' });
w = mk('div', { role: 'textbox' }, { children: [ceFirst, taSecond] });
check('priority_textarea_over_contenteditable', _miniyuResolveHost(w), 'TA_SECOND');
// textarea disabled -> input 补位
let taDis = mk('textarea', {}, { disabled: true, mkname: 'TA_DIS' });
let inVis = mk('input', { type: 'text' }, { mkname: 'IN_VIS' });
w = mk('div', { role: 'textbox' }, { children: [taDis, inVis] });
check('priority_disabled_textarea_falls_to_input', _miniyuResolveHost(w), 'IN_VIS');

// 11. wrapper 重渲染后再 resolve：换成新宿主
let oldHost = mk('textarea', {}, { mkname: 'OLD_HOST' });
w = mk('div', { role: 'textbox' }, { children: [oldHost] });
const h1 = _miniyuResolveHost(w);
let newHost = mk('div', { contenteditable: 'true' }, { mkname: 'NEW_HOST' });
w.children = [newHost]; newHost.parentElement = w;   // 模拟重渲染替换子节点
const h2 = _miniyuResolveHost(w);
results.push({ label: 'rerender_re_resolves', ok: (h2 && h2._mkname === 'NEW_HOST' && h2._mkname !== h1._mkname),
               got: h2 ? h2.tagName + ':' + h2._mkname : 'null', expect: 'NEW_HOST' });

let fail = 0;
for (const r of results) {
  if (r.ok) console.log('OK|' + r.label);
  else { fail = 1; console.log('FAIL|' + r.label + '|expect=' + r.expect + '|got=' + r.got); }
}
process.exit(fail);
"""


class TestJsEditHostResolution(unittest.TestCase):
    """用 Node 运行真实 _EDIT_HOST_HELPERS_JS，验证 11 个 primitive 解析场景。"""

    def test_node_resolve_primitive_cases(self):
        if shutil_which("node") is None:
            self.skipTest("node 不可用")
        src = "(() => { " + _EDIT_HOST_HELPERS_JS + "\n" + _NODE_CASES + "\n})()"
        p = subprocess.run(["node", "-e", src], capture_output=True, text=True,
                           cwd=str(Path(__file__).resolve().parent))
        out = (p.stdout or "").strip()
        lines = [ln for ln in out.splitlines() if ln]
        self.assertEqual(p.returncode, 0, msg=out or p.stderr)
        fails = [ln for ln in lines if ln.startswith("FAIL")]
        self.assertEqual(fails, [])
        oks = [ln for ln in lines if ln.startswith("OK")]
        # 11 个解析场景（含子断言：only_hidden/only_readonly/only_disabled 等）
        self.assertGreaterEqual(len(oks) + len(fails), 15, msg=out)


def shutil_which(name):
    from shutil import which
    return which(name)


class TestTypeInternalDeterministicRecovery(unittest.TestCase):
    """browser_type 工具内确定性 recovery：wrapper not_editable → 自动下钻宿主，不回 LLM。"""

    def setUp(self):
        self.fb = _Fake()
        self.fb.type_state = {
            "eid:wrapper": {"ok": False, "reason": "not_editable"},
            "eid:host":    {"ok": True, "reason": None},
        }

    def test_wrapper_recovery_drills_to_host(self):
        self.fb.resolve_host = {"eid:wrapper": "eid:host"}
        r = self.fb.type_text("你好", target="eid:wrapper")
        # 成功信息里的 ref 是下钻后的宿主，而不是原始 wrapper
        self.assertEqual(r["ref"], "eid:host")
        self.assertEqual(self.fb._inserts[-1]["text"], "你好")
        # 只发生一次确定性的工具内重试（find/inspect/截图都没有被调用）
        self.assertEqual(len(self.fb._inserts), 1)

    def test_wrapper_recovery_when_no_host_raises(self):
        self.fb.resolve_host = {}                       # wrapper 无下钻宿主
        with self.assertRaises(LookupError) as cm:
            self.fb.type_text("你好", target="eid:wrapper")
        self.assertIn("not_editable", str(cm.exception))

    def test_non_not_editable_errors_not_recovered(self):
        # readonly / disabled：不属于"语义 wrapper"问题，不得做宿主下钻掩盖
        self.fb.type_state = {
            "eid:ro":  {"ok": False, "reason": "readonly"},
            "eid:dis": {"ok": False, "reason": "disabled"},
            "eid:h":   {"ok": True, "reason": None},
        }
        self.fb.resolve_host = {"eid:ro": "eid:h", "eid:dis": "eid:h"}
        for ref in ("eid:ro", "eid:dis"):
            with self.assertRaises(LookupError):
                self.fb.type_text("你好", target=ref)
        self.assertEqual(len(self.fb._inserts), 0)      # 未误输入到宿主

    def test_gone_response_is_stale_not_recovered(self):
        with self.assertRaises(Exception):
            self.fb.type_text("你好", target="eid:gone")


from core import agent as _agent_mod


class TestRunLogDuplicateGuard(unittest.TestCase):
    """run_log 不再重复写入同一个 LLM request；并自动校验 sum==末次 cum。"""

    def test_duplicate_fingerprint_skipped(self):
        inst = object.__new__(_agent_mod.Agent)
        inst._turn_usage_trace = []
        inst._run_consistency_done = False
        rowA = {"kind": "llm", "step": 38, "cum_total_used": 100, "total_tokens": 50,
                "prompt_tokens": 20, "completion_tokens": 30, "reasoning_tokens": 0,
                "image_tokens": 0, "message_count": 0, "image_count": 0, "char_count": 0}
        inst._turn_usage_trace.append(dict(rowA))       # 模拟第一条已落盘
        # 另一条（step 不同 → 指纹不同）不算重复
        self.assertFalse(inst._is_llm_row_duplicate({**rowA, "step": 39}))
        # 完全相同的克隆：判为重复
        self.assertTrue(inst._is_llm_row_duplicate(dict(rowA)))

    def test_finalize_consistency_marks_valid_and_invalid(self):
        from core.agent import Agent
        inst = object.__new__(Agent)
        inst._turn_usage_trace = []
        inst._run_log = None
        inst._run_consistency_done = False
        inst._append_run_log = _append_stub

        # 一致：sum(50+50)==末次 cum 100
        inst._turn_usage_trace = [
            {"kind": "llm", "step": 1, "total_tokens": 50, "cum_total_used": 50},
            {"kind": "llm", "step": 2, "total_tokens": 50, "cum_total_used": 100},
        ]
        inst._finalize_run_consistency()
        self.assertTrue(_append_stub.rows[-1]["valid"])

        # 不一致：人为制造重复（丢一条），应标记 invalid
        _append_stub.rows = []
        inst2 = object.__new__(Agent)
        inst2._turn_usage_trace = [
            {"kind": "llm", "step": 1, "total_tokens": 50, "cum_total_used": 50},
            {"kind": "llm", "step": 2, "total_tokens": 50, "cum_total_used": 50},  # 重复快照
        ]
        inst2._run_log = None
        inst2._run_consistency_done = False
        inst2._append_run_log = _append_stub
        inst2._finalize_run_consistency()
        self.assertFalse(_append_stub.rows[-1]["valid"])
        self.assertIn("INVALID", _append_stub.rows[-1]["note"])

        # 幂等：第二次调用不再追加
        n = len(_append_stub.rows)
        inst2._finalize_run_consistency()
        self.assertEqual(len(_append_stub.rows), n)


def _append_stub(rec):
    if not hasattr(_append_stub, "rows"):
        _append_stub.rows = []
    _append_stub.rows.append(rec)


if __name__ == "__main__":
    unittest.main(verbosity=2)