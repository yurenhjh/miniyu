"""
test_app_tools.py
第4组：应用操作工具 / 技能测试

验证新增的 7 个应用操作工具与 2 个应用操作技能：
- 工具：list_windows / find_window / activate_window / send_hotkey / send_text / take_screenshot / click_at
- 技能：app_open / app_send_message

用 Mock 实现验证操作契约（联调阶段不依赖真实窗口 / 桌面环境）。
"""

import unittest

from mock import MockToolRegistry, MockSkillLibrary


class TestAppTools(unittest.TestCase):
    """应用操作工具（Mock 契约）"""

    def setUp(self):
        self.registry = MockToolRegistry()

    def test_app_tools_registered(self):
        tools = self.registry.list_tools()
        for t in ("list_windows", "find_window", "activate_window",
                  "send_hotkey", "send_text", "take_screenshot", "click_at"):
            self.assertIn(t, tools)

    def test_list_windows_shape(self):
        r = self.registry.call("list_windows", {})
        self.assertTrue(r["success"])
        self.assertIsInstance(r["result"], list)
        self.assertEqual(r["result"][0]["title"], "QQ")

    def test_find_window_shape(self):
        r = self.registry.call("find_window", {"process": "QQ"})
        self.assertTrue(r["success"])
        self.assertEqual(r["result"]["hwnd"], 101)
        self.assertIn("title", r["result"])
        self.assertIn("pid", r["result"])

    def test_activate_window(self):
        r = self.registry.call("activate_window", {"hwnd": 101})
        self.assertTrue(r["success"])
        self.assertIn("[MOCK]", r["result"])

    def test_send_hotkey(self):
        r = self.registry.call("send_hotkey", {"keys": ["ctrl", "f"]})
        self.assertTrue(r["success"])
        self.assertIn("ctrl+f", r["result"])

    def test_send_text_chinese(self):
        r = self.registry.call("send_text", {"text": "你好"})
        self.assertTrue(r["success"])
        self.assertIn("你好", r["result"])

    def test_take_screenshot(self):
        r = self.registry.call("take_screenshot", {})
        self.assertTrue(r["success"])

    def test_click_at(self):
        r = self.registry.call("click_at", {"x": 100, "y": 200})
        self.assertTrue(r["success"])
        self.assertIn("100", r["result"])
        self.assertIn("200", r["result"])


class TestAppSkills(unittest.TestCase):
    """应用操作技能（Mock 契约）"""

    def setUp(self):
        self.skills = MockSkillLibrary()

    def test_app_skills_registered(self):
        skills = self.skills.list_skills()
        self.assertIn("app_open", skills)
        self.assertIn("app_send_message", skills)

    def test_app_open_skill(self):
        r = self.skills.app_open("QQ")
        self.assertTrue(r["activated"])
        self.assertEqual(r["window"]["title"], "QQ")

    def test_app_send_message_skill(self):
        r = self.skills.app_send_message("QQ", "一中兄弟会", "你好")
        self.assertEqual(r["search_keyword"], "一中兄弟会")
        self.assertEqual(r["message"], "你好")
        self.assertIn("window", r)
        self.assertIn("screenshot", r)


class TestBrowserTools(unittest.TestCase):
    """浏览器结构化工具（Mock 契约）"""

    def setUp(self):
        self.registry = MockToolRegistry()

    def test_browser_tools_registered(self):
        tools = self.registry.list_tools()
        for t in ("browser_launch", "browser_close", "browser_navigate",
                  "browser_snapshot", "browser_click", "browser_type",
                  "browser_read_text", "browser_screenshot", "browser_wait"):
            self.assertIn(t, tools)

    def test_browser_snapshot_shape(self):
        r = self.registry.call("browser_snapshot", {})
        self.assertTrue(r["success"])
        self.assertIsInstance(r["result"], list)
        self.assertIn("index", r["result"][0])
        self.assertIn("tag", r["result"][0])
        self.assertIn("text", r["result"][0])

    def test_browser_click_by_index(self):
        r = self.registry.call("browser_click", {"index": 1})
        self.assertTrue(r["success"])
        self.assertTrue(r["result"]["clicked"])
        self.assertEqual(r["result"]["index"], 1)

    def test_browser_type_chinese(self):
        r = self.registry.call("browser_type", {"text": "你好", "index": 0})
        self.assertTrue(r["success"])
        self.assertEqual(r["result"]["typed"], "你好")

    def test_browser_navigate(self):
        r = self.registry.call("browser_navigate", {"url": "https://example.com"})
        self.assertTrue(r["success"])
        self.assertEqual(r["result"]["url"], "https://example.com")


class TestBrowserSkills(unittest.TestCase):
    """浏览器组合技能（Mock 契约）"""

    def setUp(self):
        self.skills = MockSkillLibrary()

    def test_browser_skills_registered(self):
        skills = self.skills.list_skills()
        self.assertIn("browser_search", skills)
        self.assertIn("browser_extract", skills)

    def test_browser_search_skill(self):
        r = self.skills.browser_search("人工智能", engine="bing")
        self.assertEqual(r["query"], "人工智能")
        self.assertEqual(r["engine"], "bing")
        self.assertIn("text_snippet", r)

    def test_browser_extract_skill(self):
        r = self.skills.browser_extract("https://example.com")
        self.assertEqual(r["url"], "https://example.com")
        self.assertIn("text", r)


if __name__ == "__main__":
    unittest.main()