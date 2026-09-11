"""
test_uitarget.py — P0 统一 UI 目标数据模型测试

覆盖 ref 解析/生成、som 编号解析、CSS 坐标助手、UITarget 构造与摘要。
纯逻辑，无浏览器依赖。
"""

import unittest

from core.uitarget import (
    UITarget, bbox_to_device, css_center, format_ref, is_ref, is_som,
    parse_ref, parse_som, summarize_targets,
)


class TestRefParsing(unittest.TestCase):
    def test_format_and_parse_with_frame(self):
        self.assertEqual(format_ref("17", "1"), "f1e17")
        self.assertEqual(parse_ref("f1e17"), ("1", "17"))

    def test_format_and_parse_no_frame(self):
        self.assertEqual(format_ref("id-search"), "eid-search")
        self.assertEqual(parse_ref("eid-search"), (None, "id-search"))

    def test_parse_ref_invalid(self):
        self.assertEqual(parse_ref("e17"), (None, "17"))
        self.assertEqual(parse_ref("17"), (None, None))
        self.assertEqual(parse_ref(123), (None, None))
        self.assertEqual(parse_ref("hello"), (None, None))

    def test_is_ref(self):
        self.assertTrue(is_ref("e17"))
        self.assertTrue(is_ref("f1e17"))
        self.assertFalse(is_ref("hello"))
        self.assertFalse(is_ref(None))


class TestSomParsing(unittest.TestCase):
    def test_parse_som(self):
        self.assertEqual(parse_som("som:2"), 2)
        self.assertEqual(parse_som("som:3"), 3)  # 容忍前后空格
        self.assertEqual(parse_som("som:abc"), None)
        self.assertEqual(parse_som("e17"), None)

    def test_is_som(self):
        self.assertTrue(is_som("som:7"))
        self.assertFalse(is_som("e17"))
        self.assertFalse(is_som(None))


class TestCoordinateHelpers(unittest.TestCase):
    def test_css_center(self):
        self.assertEqual(css_center([0, 0, 100, 40]), [50.0, 20.0])
        self.assertIsNone(css_center(None))
        self.assertIsNone(css_center([1, 2]))

    def test_bbox_to_device(self):
        self.assertEqual(bbox_to_device([0, 0, 100, 40], 2.0), [0.0, 0.0, 200.0, 80.0])
        self.assertIsNone(bbox_to_device(None, 2.0))


class TestUITarget(unittest.TestCase):
    def test_from_js_builds_center(self):
        t = UITarget.from_js({
            "ref": "eid:search", "tag": "input", "role": "textbox",
            "name": "搜索", "x": 10, "y": 20, "w": 200, "h": 40,
        }, dpr=2.0, source="browser_som")
        self.assertEqual(t.source, "browser_som")
        self.assertEqual(t.bbox_css, [10, 20, 200, 40])
        self.assertEqual(t.center_css, [110.0, 40.0])
        self.assertIsNone(t.visual_num)

    def test_to_dict_schema(self):
        t = UITarget.from_js({
            "ref": "eid:login", "tag": "a", "role": "link", "name": "登录",
            "x": 0, "y": 0, "w": 40, "h": 20,
        }, source="browser_dom")
        t.visual_num = 3
        d = t.to_dict()
        self.assertEqual(d["num"], 3)
        self.assertEqual(d["ref"], "eid:login")
        self.assertIn("bbox_css", d)
        self.assertIn("center_css", d)

    def test_as_summary_includes_num_and_ref(self):
        t = UITarget.from_js({
            "ref": "e17", "tag": "button", "role": "button", "name": "发送",
            "x": 0, "y": 0, "w": 80, "h": 40,
        }, source="browser_som")
        t.visual_num = 1
        s = t.as_summary()
        self.assertIn("[1]", s)
        self.assertIn("发送", s)
        self.assertIn("e17", s)


class TestSummarize(unittest.TestCase):
    def test_summarize_targets_lines(self):
        a = UITarget.from_js({"ref": "e1", "tag": "button", "role": "button",
                              "name": "A", "x": 0, "y": 0, "w": 10, "h": 10})
        a.visual_num = 1
        b = UITarget.from_js({"ref": "e2", "tag": "a", "role": "link",
                              "name": "B", "x": 0, "y": 0, "w": 10, "h": 10})
        b.visual_num = 2
        text = summarize_targets([a, b])
        self.assertEqual(text.count("\n"), 1)
        self.assertIn("A", text)
        self.assertIn("B", text)


if __name__ == "__main__":
    unittest.main()