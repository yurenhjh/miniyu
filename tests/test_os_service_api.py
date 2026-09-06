"""
test_os_service_api.py
第4组：接口联调测试
模拟第3组AppAgent → OSServiceAPI → ToolRegistry / SkillLibrary 的完整调用链
"""

import os
import tempfile
import unittest

from core.os_service_api import OSServiceAPI


class TestOSServiceAPI(unittest.TestCase):
    """OSServiceAPI 集成测试"""

    def setUp(self):
        self.api = OSServiceAPI()
        self.test_dir = tempfile.mkdtemp()
        self.file1 = os.path.join(self.test_dir, "test.txt")
        self.file2 = os.path.join(self.test_dir, "copy.txt")
        with open(self.file1, "w", encoding="utf-8") as f:
            f.write("Agentic OS Test")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # =========================
    # Tool 接口测试
    # =========================

    def test_execute_tool_copy_file(self):
        """AppAgent调用copy_file"""
        result = self.api.execute_tool("copy_file", {
            "src": self.file1,
            "dest": self.file2
        })
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(self.file2))

    def test_execute_tool_file_exists(self):
        """AppAgent调用file_exists"""
        result = self.api.execute_tool("file_exists", {"path": self.file1})
        self.assertTrue(result["success"])
        self.assertTrue(result["result"])

    def test_execute_tool_unknown(self):
        """AppAgent调用未知工具"""
        result = self.api.execute_tool("unknown_tool", {})
        self.assertFalse(result["success"])

    def test_execute_tool_write_and_read(self):
        """AppAgent调用write_text_file后读取"""
        path = os.path.join(self.test_dir, "hello.txt")
        write_result = self.api.execute_tool("write_text_file", {
            "path": path,
            "content": "Hello Group4"
        })
        self.assertTrue(write_result["success"])

        read_result = self.api.execute_tool("read_text_file", {"path": path})
        self.assertTrue(read_result["success"])
        self.assertEqual(read_result["result"], "Hello Group4")

    def test_list_available_tools(self):
        """列出可用工具"""
        tools = self.api.list_available_tools()
        self.assertIn("copy_file", tools)
        self.assertIn("run_command", tools)
        self.assertGreaterEqual(len(tools), 13)

    # =========================
    # Skill 接口测试
    # =========================

    def test_run_skill_system_info(self):
        """调用系统信息技能"""
        result = self.api.run_skill("system_info")
        self.assertTrue(result["success"])
        self.assertEqual(result["skill"], "system_info")

    def test_run_skill_organize_downloads(self):
        """调用下载整理技能（无效路径应返回错误）"""
        result = self.api.run_skill("organize_downloads", {
            "path": "/nonexistent_test_path_xyz"
        })
        self.assertFalse(result["success"])

    def test_invalid_skill(self):
        """调用不存在的技能"""
        result = self.api.run_skill("abc_skill")
        self.assertFalse(result["success"])

    def test_list_available_skills(self):
        """列出可用技能"""
        skills = self.api.list_available_skills()
        self.assertIn("system_info", skills)
        self.assertIn("organize_downloads", skills)

    # =========================
    # 统计功能测试
    # =========================

    def test_get_tool_stats_empty(self):
        """调用统计（无调用时）"""
        stats = self.api.get_tool_stats()
        self.assertEqual(stats["total_calls"], 0)

    def test_get_tool_stats_after_calls(self):
        """调用统计（多次调用后）"""
        self.api.execute_tool("copy_file", {"src": self.file1, "dest": self.file2})
        self.api.execute_tool("file_exists", {"path": self.file1})
        self.api.execute_tool("file_exists", {"path": self.file2})

        stats = self.api.get_tool_stats()
        self.assertEqual(stats["total_calls"], 3)
        self.assertEqual(stats["by_tool"]["file_exists"], 2)
        self.assertEqual(stats["by_tool"]["copy_file"], 1)
        self.assertGreaterEqual(stats["success_rate"], 0)


if __name__ == "__main__":
    unittest.main()
