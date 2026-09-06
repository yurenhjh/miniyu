# 第4组：core/web_search.py（HTTP 联网搜索）纯离网单测——喂手写样本 HTML，不发真实请求
# 背景：browser_search 由"无头浏览器打字+回车"改为 HTTP 直连 bing（CDP 合成回车不触发提交，
#       曾导致搜索超时堆到数分钟）。这里只测解析/取数/异常，不碰网络。
import unittest
from unittest import mock

from core import web_search

# 一段仿必应结果页的样本 HTML：两条可解析、一条相对链接应被跳过、含实体/不可见空白
SAMPLE_HTML = """<html><body>
<ol id="b_results">
<li class="b_algo">
  <h2><a href="https://example.com/foo?a=1&amp;b=2">Foo &amp; Bar 标题</a></h2>
  <div class="b_caption"><p>摘要文字 <b>加粗</b> &nbsp;带空格 &ensp;内容</p></div>
</li>
<li class="b_algo">
  <h2><a href="/search?q=skip">相对链接应跳过</a></h2>
  <p>这条不该被收进来</p>
</li>
<li class="b_algo">
  <h2><a href="https://example.org/">二 &mdash; 标题</a></h2>
  <p>第二段<b>摘要</b>。</p>
</li>
</ol>
</body></html>"""

NO_RESULT_HTML = "<html><body><div id=\"b_results\"></div></body></html>"


class TestParseBingHtml(unittest.TestCase):
    """parse_bing_html：结构解析、清洗、相对链接过滤、top 截断"""

    def test_parse_returns_title_url_snippet(self):
        out = web_search.parse_bing_html(SAMPLE_HTML, top=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["title"], "Foo & Bar 标题")
        self.assertEqual(out[0]["url"], "https://example.com/foo?a=1&b=2")
        # 实体与 &nbsp;/&ensp; 都被清洗折叠成普通空格
        self.assertEqual(out[0]["snippet"], "摘要文字 加粗 带空格 内容")

    def test_skips_relative_link_and_unescapes_mdash(self):
        out = web_search.parse_bing_html(SAMPLE_HTML, top=6)
        urls = [it["url"] for it in out]
        self.assertNotIn("/search?q=skip", urls)   # 相对链接被过滤
        self.assertIn("example.org", urls[1])
        self.assertEqual(out[1]["title"], "二 — 标题")
        self.assertEqual(out[1]["snippet"], "第二段摘要。")

    def test_empty_or_no_algo_page(self):
        self.assertEqual(web_search.parse_bing_html("", top=3), [])
        self.assertEqual(web_search.parse_bing_html(NO_RESULT_HTML, top=3), [])

    def test_top_cap(self):
        out = web_search.parse_bing_html(SAMPLE_HTML, top=1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "Foo & Bar 标题")


class TestSearch(unittest.TestCase):
    """search：组装/取数/摘要截断/异常（_fetch 打桩，不联网）"""

    @staticmethod
    def _fake_fetch(url, timeout=web_search.TIMEOUT):
        return "https://cn.bing.com/search?q=xxx", SAMPLE_HTML

    def test_search_shape_and_text(self):
        with mock.patch("core.web_search._fetch", self._fake_fetch):
            res = web_search.search("测试关键词", top=2)
        self.assertEqual(res["engine"], "bing")
        self.assertEqual(len(res["results"]), 2)
        self.assertEqual(res["url"], "https://cn.bing.com/search?q=xxx")
        # text_snippet 是人读文本，应含标题与链接
        self.assertIn("Foo & Bar 标题", res["text_snippet"])
        self.assertIn("https://example.com/foo?a=1&b=2", res["text_snippet"])

    def test_bing_url_quotes_query(self):
        # 直连 cn.bing.com：www 会 302 跳 cn，CN 网络下二次握手偶发被重置
        u = web_search.bing_url("CS2 BLAST 2026")
        self.assertTrue(u.startswith("https://cn.bing.com/search?q="))
        self.assertIn("CS2%20BLAST%202026", u)

    def test_empty_query_rejected(self):
        with self.assertRaises(web_search.WebSearchError):
            web_search.search("   ")

    def test_baidu_not_supported(self):
        # 百度有"安全验证"人机墙，接入 bing 之外一律明确报错，避免模型傻等
        with self.assertRaises(web_search.WebSearchError):
            web_search.search("x", engine="baidu")

    def test_no_results_raises(self):
        with mock.patch(
            "core.web_search._fetch",
            lambda url, timeout=web_search.TIMEOUT: ("https://cn.bing.com/", NO_RESULT_HTML),
        ):
            with self.assertRaises(web_search.WebSearchError):
                web_search.search("空结果词", top=3)

    def test_network_error_wrapped(self):
        # 网络异常发生在 _fetch 内部：打桩 urlopen 抛超时，重试耗尽后包装成 WebSearchError
        with mock.patch.object(
            web_search.urllib.request, "urlopen",
            side_effect=TimeoutError("socket timed out"),
        ):
            with self.assertRaises(web_search.WebSearchError):
                web_search.search("超时词", top=3)

    def test_transient_reset_retries_then_succeeds(self):
        # CN 网络下 bing 偶发重置连接：第一次 urlopen 抛瞬断，第二次成功 → search 应照常出结果
        class FakeResp:
            def __init__(self, data):
                self._data = data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return self._data

            def geturl(self):
                return "https://cn.bing.com/search?q=xx"

            @property
            def headers(self):
                class _H:
                    def get(self, _k, _d=None):
                        return None

                return _H()

        with mock.patch.object(
            web_search.urllib.request, "urlopen",
            side_effect=[ConnectionResetError("reset"), FakeResp(SAMPLE_HTML.encode("utf-8"))],
        ) as m:
            res = web_search.search("重试词", top=2)
        self.assertEqual(len(res["results"]), 2)
        self.assertEqual(m.call_count, 2)   # 确认确实走了一次重试


if __name__ == "__main__":
    unittest.main()
