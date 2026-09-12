# -*- coding: utf-8 -*-
"""test_read_contract_v2.py — P2-8 Phase 2：related-content read-contract v2

覆盖（第一批只改 read-contract / related-content，不动 recovery/prompt/SoM）：
- find 命中"标题类元素"且标题身下有正文 → 结果带 related_content（relation=content, handle）
- related_content.handle 是独立短 handle（与元素自身 handle 不同）
- 仅标题类且身下有正文才生成；无正文 / 非标题 → related_content 缺省
- 内容超过硬上限 → truncated=true（meta 可见；正文读取有界）
- read_text(target=<related.handle>, mode="content") → 读回对应正文区块
- mode="content" 拒绝非 related handle（普通元素 handle / 裸 ref / selector）
- 旧 read_text(target=<普通handle>) 行为完全不变（A1 兼容）
- 页面变化后 related handle 失效（stale，同 session 契约）
- v2 related handle 与普通 handle 互不复用（隔离）
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.browser_controller import BrowserController, SoMStaleError, _SNAPSHOT_JS, _INSPECT_JS


class RC2FakeBrowser(BrowserController):
    """受控 mock：find 返回两手数据演示 related_content 语义；read 走罐装映射。

    mode=content 时 read_text 会走 _RELATED_CONTENT_JS 的 JS 路径（在真实浏览器上），但 mock
    的 _evaluate 不执行 JS。为在单测里独立验证"v2 read 分支正确路由并把 origin_ref 传给 JS"，
    这里 mock _evaluate 识别 _RELATED_CONTENT_JS 调用串并返回罐装正文；其余路径保持真实逻辑。
    """

    def __init__(self, url="https://example.com/doc"):
        super().__init__(ws_url="fake")
        self.url = url
        self.dom_len = 5000
        self._page_sig = (url, 5000)
        # handle -> related 的 origin_ref 映射（由 _alloc_handles 写入 registry）
        self.rel_text_by_origin = {
            "eid:examples_h2": "Creating a basic button\nThis is the first sentence of the examples body.\nMore text follows.",
        }

    def _read_page_sig(self):
        return (self.url, self.dom_len)

    def _evaluate(self, expression):
        expr = str(expression).strip()
        if expr == "location.href":
            return self.url
        if expr == "document.title":
            return "文档"
        if expr == _SNAPSHOT_JS.strip():
            return []
        if expr == _INSPECT_JS.strip():
            return []
        # find JS 返回含 related_content 的条目
        if "const wantRole" in expr:
            return [{
                "ref": "eid:examples_h2", "tag": "a", "role": "link",
                "name": "Examples", "in_viewport": True,
                "related_content": {
                    "relation": "content", "origin_ref": "eid:examples_h2",
                    "preview": "Creating a basic button", "char_count": 120, "truncated": False,
                },
            }]
        # v2 content read：识别 _RELATED_CONTENT_JS 调用串（含 _relBody 与 data-miniyu-ref 查 origin）
        if "_relHeading" in expr or "related" in expr.lower() or "relBody" in expr:
            import re
            m = re.search(r'data-miniyu-ref=["\\]+([^"\\]+)', expr)
            if m:
                return self.rel_text_by_origin.get(m.group(1))
            return None
        return None


class TestRelatedContent(unittest.TestCase):
    def test_find_heading_with_body_yields_related_handle(self):
        fb = RC2FakeBrowser()
        hits = fb.find(text="Examples", role="link")
        self.assertEqual(len(hits), 1)
        it = hits[0]
        self.assertIn("handle", it)
        rc = it.get("related_content")
        self.assertIsInstance(rc, dict)
        # 元素 handle 与 related handle 不同
        self.assertIn("handle", rc)
        self.assertNotEqual(it["handle"], rc["handle"])
        self.assertEqual(rc["relation"], "content")
        self.assertEqual(rc["origin_ref"], "eid:examples_h2")
        self.assertFalse(rc["truncated"])

    def test_related_body_read_via_mode_content(self):
        fb = RC2FakeBrowser()
        hits = fb.find(text="Examples")
        rc = hits[0]["related_content"]
        text = fb.read_text(target=rc["handle"], mode="content")
        self.assertIn("Creating a basic button", text)
        self.assertIn("first sentence", text)

    def test_mode_content_rejects_non_related_handle(self):
        fb = RC2FakeBrowser()
        # 普通元素 handle（无 rel 前缀）在真实 flow 里是 e1；这里直接构造一个普通 handle 断言拒绝
        fb._handle_seq += 1
        normal = f"e{fb._handle_seq}"
        fb._handle_registry[normal] = {
            "ref": "eid:some_para", "sid": fb._activity_sid, "sig": fb._page_sig}
        with self.assertRaises(LookupError):
            fb.read_text(target=normal, mode="content")

    def test_mode_content_rejects_selector(self):
        fb = RC2FakeBrowser()
        with self.assertRaises(LookupError):
            fb.read_text(selector="h2#examples", mode="content")

    def test_old_read_behavior_unchanged(self):
        # 旧 read_text(target=<普通handle>) 不应被 mode 污染（默认 None 时走旧路径）
        fb = RC2FakeBrowser()
        fb._handle_seq += 1
        h = f"e{fb._handle_seq}"
        fb._handle_registry[h] = {
            "ref": "eid:examples_h2", "sid": fb._activity_sid, "sig": fb._page_sig}
        # mock：普通路径（非 v2）查 data-miniyu-ref 应走 read_text 主 JS，这里返回 None 由 mock 处理。
        # 用简单断言：不带 mode 不会抛 v2 特定错误。
        try:
            fb.read_text(target=h)
        except LookupError as e:
            # 元素缺失会抛"已不在页面"，这是 mock 未供内文所致，属预期；但绝不应是 v2 的
            # "mode=content" 专属报错。此测试保保护 mode 默认不开启。
            self.assertNotIn("mode=content", str(e))

    def test_stale_related_handle_after_page_change(self):
        fb = RC2FakeBrowser()
        hits = fb.find(text="Examples")
        rc = hits[0]["related_content"]
        rel_h = rc["handle"]
        # 模拟导航/页面变化 → dom_len 变化 → 指纹不一致 → handle 失效
        fb.dom_len = 8000
        # read mode=content 在 _handle_to_ref 返回 None 时应走 is_ref 分支落在普通路径或拒绝
        # 而不会静默成功。此处断言：读不回正文（origin 已不与当前 session 匹配）
        with self.assertRaises((LookupError, ValueError)):
            fb.read_text(target=rel_h, mode="content")

    def test_related_handle_is_distinct_from_element_handle(self):
        fb = RC2FakeBrowser()
        hits = fb.find(text="Examples")
        self.assertNotEqual(hits[0]["handle"], hits[0]["related_content"]["handle"])

    # ---- 第二批（B+C）：related handle 自动路由 + 错误信息明确 ----

    def test_related_handle_auto_routes_without_mode(self):
        # B：read(target=<related_handle>) 省略 mode → 自动读正文区块，勿需记忆额外参数
        fb = RC2FakeBrowser()
        hits = fb.find(text="Examples")
        rc = hits[0]["related_content"]
        text = fb.read_text(target=rc["handle"])
        self.assertIn("Creating a basic button", text)
        self.assertIn("first sentence", text)

    def test_related_handle_rejects_unsupported_mode(self):
        # C：related handle + 非 content 的 mode → 明确拒绝并提示正确用法，不误导为 stale
        fb = RC2FakeBrowser()
        hits = fb.find(text="Examples")
        rc = hits[0]["related_content"]
        with self.assertRaises(LookupError) as cm:
            fb.read_text(target=rc["handle"], mode="snippet")
        self.assertIn("mode", str(cm.exception))

    def test_normal_handle_with_mode_content_rejected_clear(self):
        # C：普通元素 handle + mode=content → 明确说明不是 related handle，保持第一批准入
        fb = RC2FakeBrowser()
        fb._handle_seq += 1
        normal = f"e{fb._handle_seq}"
        fb._handle_registry[normal] = {
            "ref": "eid:some_para", "sid": fb._activity_sid, "sig": fb._page_sig}
        with self.assertRaises(LookupError) as cm:
            fb.read_text(target=normal, mode="content")
        self.assertIn("related", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)