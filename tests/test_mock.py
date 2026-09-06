"""
test_mock.py
第4组：Mock 模块单元测试
验证Mock实现与正式接口的兼容性
"""

import unittest
from mock import MockToolRegistry, MockSkillLibrary, MockOSServiceAPI


class TestMockToolRegistry(unittest.TestCase):
    """MockToolRegistry 单元测试"""

    def setUp(self):
        self.registry = MockToolRegistry()

    def test_list_tools_count(self):
        """Mock工具数量应与正式版一致（57个）"""
        tools = self.registry.list_tools()
        self.assertEqual(len(tools), 57)

    def test_list_tools_contains_core_tools(self):
        """Mock应包含核心工具"""
        tools = self.registry.list_tools()
        for essential in ["copy_file", "move_path", "delete_file",
                          "create_directory", "list_directory", "run_command"]:
            self.assertIn(essential, tools)

    def test_mock_copy_file(self):
        """Mock复制文件"""
        result = self.registry.call("copy_file", {"src": "a.txt", "dest": "b.txt"})
        self.assertTrue(result["success"])
        self.assertIn("[MOCK]", result["result"])

    def test_mock_move_file(self):
        """Mock移动文件"""
        result = self.registry.call("move_path", {"src": "a.txt", "dest": "b.txt"})
        self.assertTrue(result["success"])
        self.assertIn("[MOCK]", result["result"])

    def test_mock_delete_file(self):
        """Mock删除文件"""
        result = self.registry.call("delete_file", {"path": "/tmp/a.txt"})
        self.assertTrue(result["success"])

    def test_mock_rename_file(self):
        """Mock重命名文件"""
        result = self.registry.call("rename_path", {"src": "a.txt", "dest": "b.txt"})
        self.assertTrue(result["success"])

    def test_mock_create_folder(self):
        """Mock创建目录"""
        result = self.registry.call("create_directory", {"path": "/tmp/newdir"})
        self.assertTrue(result["success"])

    def test_mock_delete_folder(self):
        """Mock删除目录"""
        result = self.registry.call("delete_directory", {"path": "/tmp/olddir"})
        self.assertTrue(result["success"])

    def test_mock_list_directory(self):
        """Mock列出目录"""
        result = self.registry.call("list_directory", {"path": "/tmp"})
        self.assertTrue(result["success"])
        self.assertIsInstance(result["result"], list)

    def test_mock_file_exists(self):
        """Mock文件存在检查"""
        result = self.registry.call("file_exists", {"path": "/tmp/a.txt"})
        self.assertTrue(result["success"])
        self.assertTrue(result["result"])

    def test_mock_get_file_size(self):
        """Mock获取文件大小"""
        result = self.registry.call("get_file_size", {"path": "/tmp/a.txt"})
        self.assertTrue(result["success"])
        self.assertEqual(result["result"], 1024)

    def test_mock_read_text_file(self):
        """Mock读取文本文件"""
        result = self.registry.call("read_text_file", {"path": "/tmp/a.txt"})
        self.assertTrue(result["success"])
        self.assertIn("模拟内容", result["result"])

    def test_mock_write_text_file(self):
        """Mock写入文本文件"""
        result = self.registry.call("write_text_file",
                                    {"path": "/tmp/a.txt", "content": "hello"})
        self.assertTrue(result["success"])
        self.assertIn("[MOCK]", result["result"])

    def test_mock_current_directory(self):
        """Mock获取当前路径"""
        result = self.registry.call("current_directory", {})
        self.assertTrue(result["success"])
        self.assertIn("[MOCK]", result["result"])

    def test_mock_run_command(self):
        """Mock执行系统命令"""
        result = self.registry.call("run_command", {"cmd": "ls -la"})
        self.assertTrue(result["success"])
        self.assertIn("[MOCK]", result["result"]["stdout"])

    def test_mock_unregister(self):
        """Mock注销工具"""
        self.registry.unregister("copy_file")
        self.assertNotIn("copy_file", self.registry.list_tools())

    def test_mock_index_files(self):
        """Mock索引文件"""
        result = self.registry.call("index_files", {"path": "/tmp"})
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "index_files")
        self.assertNotEqual(result["result"], [])

    def test_mock_manage_archive(self):
        """Mock压缩/解压工具"""
        compress = self.registry.call("manage_archive", {"src_dir": "/tmp"})
        self.assertTrue(compress["success"])
        self.assertEqual(compress["tool"], "manage_archive")
        self.assertIn("[MOCK]", compress["result"])

        extract = self.registry.call(
            "manage_archive", {"action": "extract", "dest_zip": "/tmp/a.zip"})
        self.assertTrue(extract["success"])
        self.assertIn("[MOCK]", extract["result"])

    def test_mock_new_tools_smoke(self):
        """Mock新增工具冒烟（元数据/哈希/磁盘/批量/图片）"""
        cases = [
            ("create_file", {"path": "/tmp/new.txt"}),
            ("get_path_metadata", {"path": "/tmp/a.txt"}),
            ("get_directory_metadata", {"path": "/tmp"}),
            ("check_file_access", {"path": "/tmp/a.txt"}),
            ("file_checksum", {"path": "/tmp/a.txt"}),
            ("compare_files", {"path1": "/tmp/a", "path2": "/tmp/b"}),
            ("batch_copy", {"files": ["/tmp/a"], "dest_dir": "/tmp/out"}),
            ("batch_move", {"files": ["/tmp/a"], "dest_dir": "/tmp/out"}),
            ("disk_usage", {}),
            ("directory_size", {"path": "/tmp"}),
            ("find_empty_files", {"directory": "/tmp"}),
            ("find_empty_directories", {"directory": "/tmp"}),
            ("find_old_files", {"directory": "/tmp"}),
            ("get_image_metadata", {"path": "/tmp/i.png"}),
            ("rotate_image", {"path": "/tmp/i.png", "degrees": 90}),
        ]
        for name, kw in cases:
            result = self.registry.call(name, kw)
            self.assertTrue(result["success"], f"{name} should succeed")

    def test_mock_unknown_tool(self):
        """Mock调用不存在的工具"""
        result = self.registry.call("unknown_tool", {})
        self.assertFalse(result["success"])
        self.assertEqual(result["error_code"], "TOOL_NOT_FOUND")

    def test_mock_browser_tools_smoke(self):
        """Mock浏览器结构化工具冒烟"""
        close = self.registry.call("browser_close", {})
        self.assertTrue(close["success"])

        nav = self.registry.call("browser_navigate", {"url": "https://www.bing.com/"})
        self.assertTrue(nav["success"])
        self.assertEqual(nav["result"]["url"], "https://www.bing.com/")

        snap = self.registry.call("browser_snapshot", {})
        self.assertTrue(snap["success"])
        self.assertIsInstance(snap["result"], list)

        click = self.registry.call("browser_click", {"index": 1})
        self.assertTrue(click["success"])
        self.assertTrue(click["result"]["clicked"])

        typ = self.registry.call("browser_type", {"text": "你好", "index": 0})
        self.assertTrue(typ["success"])
        self.assertEqual(typ["result"]["typed"], "你好")


class TestMockSkillLibrary(unittest.TestCase):
    """MockSkillLibrary 单元测试"""

    def setUp(self):
        self.skills = MockSkillLibrary()

    def test_list_skills(self):
        """Mock技能列表"""
        skills = self.skills.list_skills()
        self.assertIn("send_email", skills)
        self.assertEqual(len(skills), 25)

    def test_organize_downloads(self):
        """Mock整理下载目录"""
        result = self.skills.organize_downloads()
        self.assertIn("[MOCK]", result)

    def test_system_info(self):
        """Mock系统信息"""
        result = self.skills.system_info()
        self.assertIn("mock", result["system"])

    def test_cleanup_temp(self):
        """Mock临时文件清理"""
        result = self.skills.cleanup_temp()
        self.assertIn("[MOCK]", result)

    def test_search_file(self):
        """Mock文件搜索"""
        result = self.skills.search_file("/home", "report")
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_extended_skills_smoke(self):
        """Mock扩展技能冒烟（8 个新技能均可调用并返回合理结构）"""
        self.assertIn("find_large_files", self.skills.list_skills())
        self.assertIn("rebuild_index", self.skills.list_skills())
        self.assertIsInstance(self.skills.summarize_files("/tmp"), dict)
        self.assertIsInstance(self.skills.find_large_files("/tmp"), list)
        self.assertIsInstance(self.skills.batch_archive(["/a", "/b"], action="compress"), dict)
        self.assertIn("_index.json", self.skills.rebuild_index("/tmp")["index_path"])
        self.assertIsInstance(self.skills.query_index("/tmp/id.json", "x"), list)
        self.assertIsInstance(self.skills.cleanup_by_type("/tmp"), list)
        self.assertIsInstance(self.skills.find_recent_files("/tmp"), list)
        self.assertIsInstance(self.skills.duplicate_finder("/tmp"), list)

    def test_agent_skills_smoke(self):
        """Mock Agent 组合型技能冒烟"""
        self.assertIn("trash_file", self.skills.list_skills())
        self.assertIn("smart_organize", self.skills.list_skills())
        self.assertIsInstance(self.skills.trash_file("/tmp/a.txt"), dict)
        self.assertIsInstance(self.skills.restore_file("/tmp/a.txt_trashed"), str)
        self.assertIsInstance(self.skills.safe_delete("/tmp/a.txt"), dict)
        self.assertIsInstance(self.skills.deduplicate_files("/tmp"), dict)
        self.assertIsInstance(self.skills.backup_file("/tmp/a.txt"), str)
        self.assertIsInstance(self.skills.restore_backup("/tmp/a.txt.bak"), str)
        self.assertIsInstance(self.skills.smart_organize("/tmp"), dict)

    def test_browser_skills_smoke(self):
        """Mock浏览器组合技能冒烟"""
        self.assertIn("browser_search", self.skills.list_skills())
        self.assertIn("browser_extract", self.skills.list_skills())
        s = self.skills.browser_search("浏览器控制", engine="bing")
        self.assertEqual(s["query"], "浏览器控制")
        self.assertIn("text_snippet", s)
        e = self.skills.browser_extract("https://example.com")
        self.assertIn("text", e)


class TestMockOSServiceAPI(unittest.TestCase):
    """MockOSServiceAPI 集成测试"""

    def setUp(self):
        self.api = MockOSServiceAPI()

    def test_execute_tool(self):
        """MockAPI工具调用"""
        result = self.api.execute_tool("copy_file",
                                       {"src": "a.txt", "dest": "b.txt"})
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "copy_file")

    def test_execute_tool_unknown(self):
        """MockAPI调用未知工具"""
        result = self.api.execute_tool("unknown_tool", {})
        self.assertFalse(result["success"])

    def test_run_skill(self):
        """MockAPI技能调用"""
        result = self.api.run_skill("system_info")
        self.assertTrue(result["success"])
        self.assertEqual(result["skill"], "system_info")

    def test_run_skill_unknown(self):
        """MockAPI调用未知技能"""
        result = self.api.run_skill("unknown_skill")
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
