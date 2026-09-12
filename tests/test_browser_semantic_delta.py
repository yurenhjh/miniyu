"""
test_browser_semantic_delta.py — P2-5 Phase D `semantic_blocks` 回归测试

覆盖（不依赖真实浏览器，用 FakeBlocks 覆盖 _evaluate/_ensure_anchor）：
- 分类规则（Python 侧）：control = suggestion 容器 || (可点击 && 短文本)
- 反例（gtp 评审 §四）：assistant 正文内含真实链接/可点击元素，只要非短/非 suggestion，必须保留为 content，不得被误删。
- baseline 历史行 / 用户刚发文本 / 时间噪声行的排除。
- assistant_reply 只拼 content 块。

分类规则已从 _BLOCKS_JS 迁到 Python 侧（如 semantic_blocks docstring 所述），故此处可直接以
结构事实(clickable/short/suggestion/tag/role) 注入做确定性测试。
"""
import unittest

from core.browser_controller import BrowserController


def _leaf(idx, text, clickable=False, short=False, suggestion=False, tag="div", role="", rect=None):
    return {"line": text, "idx": idx, "tag": tag, "role": role,
            "clickable": clickable, "short": short, "suggestion": suggestion,
            "control": (suggestion or (clickable and short)),
            "rect": rect or [10, idx * 20, 100, 18]}


class FakeBlocks(BrowserController):
    """覆盖 CDP/JS 边界：_ensure_anchor 恒真，_evaluate(_BLOCKS_JS) 返回注入的叶节点。"""

    def __init__(self, leaves):
        super().__init__(ws_url="fake")
        self._leaves = leaves

    def _ensure_anchor(self):
        return True

    def _evaluate(self, js, *a, **k):
        # 消息块探测 JS；其它调用不需要。
        return self._leaves


class TestSemanticDelta(unittest.TestCase):

    def _run(self, leaves, baseline=None):
        bc = FakeBlocks(leaves)
        return bc.semantic_blocks(baseline=baseline or {})

    # ---- 反例：正文里真实链接/可点击元素不能被误删 ----

    def test_content_link_not_misclassified(self):
        """反例（gtp §四）：assistant 正文内一个真实可点击链接（非短、非 suggestion）必须保留为 content，
        不能因 clickable=True 就被当作推荐 chip 过滤。"""
        prose = _leaf(0, "完整实现说明请看下面的仓库链接，它详细讲了方案。", clickable=False)
        link = _leaf(1, "GitHub 项目主页（深度解析全文）", clickable=True, short=False, tag="a", role="link")
        leaves = [prose, link]
        r = self._run(leaves)
        by_text = {b["text"]: b for b in r["blocks"]}
        self.assertEqual(by_text[prose["line"]]["kind"], "content")
        # 关键断言：正文里的可点击元素未被误判为 control
        self.assertEqual(by_text[link["line"]]["kind"], "content")
        self.assertIn(prose["line"], r["assistant_reply"])
        self.assertIn(link["line"], r["assistant_reply"])

    def test_long_prose_never_control_even_if_clickable(self):
        """长正文即使容器可点击也不作 control（长文本不满足 short）。"""
        leaf = _leaf(0, "这是一段很长的 assistant 正文，超过四十个字符所以不算短文本。", clickable=True, short=False)
        r = self._run([leaf])
        self.assertEqual(r["blocks"][0]["kind"], "content")
        self.assertEqual(r["assistant_reply"], leaf["line"])

    def test_suggestion_chip_is_control(self):
        """推荐 chip（suggestion 容器内）必须归 control，不进 assistant_reply。"""
        chip = _leaf(0, "你都有哪些功能？", clickable=True, short=True, suggestion=True, tag="div")
        prose = _leaf(1, "你好！有什么需要我帮忙的吗？", clickable=False)
        r = self._run([chip, prose])
        kinds = {b["text"]: b["kind"] for b in r["blocks"]}
        self.assertEqual(kinds[chip["line"]], "control")
        self.assertEqual(kinds[prose["line"]], "content")
        self.assertEqual(r["assistant_reply"], prose["line"])

    def test_short_clickable_control(self):
        """孤立短按钮/控件（可点+短、非 suggestion）按当前规则归 control。"""
        btn = _leaf(0, "确定", clickable=True, short=True, tag="button", role="button")
        prose = _leaf(1, "请确认你的问题。", clickable=False)
        r = self._run([btn, prose])
        self.assertEqual({b["text"]: b["kind"] for b in r["blocks"]}[btn["line"]], "control")
        self.assertEqual(r["assistant_reply"], prose["line"])

    # ---- baseline / user / noise 排除 ----

    def test_baseline_user_and_noise_excluded(self):
        baseline = {"leaf_lines": ["旧的历史行", "你叫什么名字？"],
                    "sem_block_count": 2, "user_message_text": "你好"}
        leaves = [
            _leaf(0, "旧的历史行"),                 # baseline 历史 → 排除
            _leaf(1, "你好"),                     # 用户刚发 → 排除
            _leaf(2, "12:08"),                   # 时间噪声 → 排除
            _leaf(3, "你好！有什么需要我帮忙的吗？"),  # 新增正文 → content
            _leaf(4, "你能做些什么？", clickable=True, short=True, suggestion=True),  # chip → control
        ]
        r = self._run(leaves, baseline=baseline)
        texts = {b["text"]: b["kind"] for b in r["blocks"]}
        self.assertNotIn("旧的历史行", texts)
        self.assertNotIn("你好", texts)
        self.assertNotIn("12:08", texts)
        self.assertNotIn("你能做些什么？", [b["text"] for b in r["blocks"] if b["kind"] == "content"])
        self.assertEqual(r["assistant_reply"], "你好！有什么需要我帮忙的吗？")

    def test_anchor_lost_returns_empty(self):
        class Lost(FakeBlocks):
            def _ensure_anchor(self):
                return False
        r = Lost([_leaf(0, "不该出现")]).semantic_blocks(baseline={})
        self.assertEqual(r, {"blocks": [], "assistant_reply": ""})

    def test_eval_exception_returns_empty_blocks(self):
        class Boom(FakeBlocks):
            def _evaluate(self, js, *a, **k):
                raise RuntimeError("js eval fail")
        r = Boom([]).semantic_blocks(baseline={})
        self.assertEqual(r["blocks"], [])
        self.assertEqual(r["assistant_reply"], "")

    def test_noise_and_nonnoise_regex(self):
        bc = FakeBlocks([])
        noise = ["11:56", "昨天 21:30", "2026-09-12", "3月5日", "今天 08:00"]
        for n in noise:
            self.assertTrue(bc._NOISE_RE.match(n), n)
        self.assertFalse(bc._NOISE_RE.match("你好！有什么需要我帮忙的吗？"))


if __name__ == "__main__":
    unittest.main()