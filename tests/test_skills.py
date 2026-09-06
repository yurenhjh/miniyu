"""
test_skills.py
第4组：SkillLibrary 单元测试
覆盖基础 4 技能 + 8 个扩展技能 + 搜索匹配模式 + Agent 组合型技能
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from core.skill_library import SkillLibrary


class TestSkillLibrary(unittest.TestCase):
    """SkillLibrary 技能库单元测试"""

    def setUp(self):
        self.skills = SkillLibrary()

    def test_list_skills(self):
        """测试技能列表"""
        skills = self.skills.list_skills()
        self.assertIn("system_info", skills)
        self.assertIn("organize_downloads", skills)
        self.assertIn("cleanup_temp", skills)
        self.assertIn("search_file", skills)
        self.assertIn("trash_file", skills)
        self.assertIn("browser_search", skills)
        self.assertIn("browser_extract", skills)
        self.assertIn("send_email", skills)
        self.assertEqual(len(skills), 25)

    def test_system_info(self):
        """测试系统信息技能"""
        result = self.skills.call("system_info")
        self.assertTrue(result["success"])
        info = result["result"]
        self.assertIn("system", info)
        self.assertIn("release", info)
        self.assertIn("machine", info)
        self.assertIn("python", info)
        self.assertIn("cpu", info)

    def test_organize_downloads_invalid_path(self):
        """测试无效路径的整理操作"""
        result = self.skills.call(
            "organize_downloads",
            {"path": "/nonexistent_path_12345"}
        )
        self.assertFalse(result["success"])

    def test_cleanup_temp(self):
        """测试临时文件清理"""
        result = self.skills.call("cleanup_temp")
        self.assertTrue(result["success"])
        self.assertIn("清理", result["result"])

    def test_search_file(self):
        """测试文件搜索技能"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            open(os.path.join(tmpdir, "test_document.txt"), "w").close()
            open(os.path.join(tmpdir, "readme.txt"), "w").close()
            os.makedirs(os.path.join(tmpdir, "sub"), exist_ok=True)
            open(os.path.join(tmpdir, "sub", "test_note.txt"), "w").close()

            result = self.skills.call(
                "search_file",
                {"directory": tmpdir, "keyword": "test"}
            )
            self.assertTrue(result["success"])
            files = result["result"]
            self.assertEqual(len(files), 2)  # test_document.txt + test_note.txt

    def test_skill_not_found(self):
        """测试不存在的技能"""
        result = self.skills.call("nonexistent_skill")
        self.assertFalse(result["success"])
        self.assertIn("不存在", result["error"])

    def test_register_custom_skill(self):
        """测试动态注册技能"""
        def my_skill():
            return "custom result"

        self.skills.register("my_custom_skill", my_skill)
        self.assertIn("my_custom_skill", self.skills.list_skills())

        result = self.skills.call("my_custom_skill")
        self.assertTrue(result["success"])
        self.assertEqual(result["result"], "custom result")


class TestExtendedSkills(unittest.TestCase):
    """8 个扩展技能单元测试（共用文件系统夹具）"""

    def setUp(self):
        self.skills = SkillLibrary()
        self.test_dir = tempfile.mkdtemp()

        # 目录结构：
        # test_dir/
        #   a.txt        (10 bytes)
        #   b.tmp        (5 bytes)
        #   big.bin      (100 bytes)
        #   sub/
        #     c.txt      (20 bytes)
        #     sub.tmp    (1 bytes)
        self.sub = os.path.join(self.test_dir, "sub")
        os.makedirs(self.sub)
        sizes = {"a.txt": 10, "b.tmp": 5, "big.bin": 100}
        for name, size in sizes.items():
            with open(os.path.join(self.test_dir, name), "wb") as f:
                f.write(b"x" * size)
        with open(os.path.join(self.sub, "c.txt"), "wb") as f:
            f.write(b"y" * 20)
        with open(os.path.join(self.sub, "sub.tmp"), "wb") as f:
            f.write(b"z")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _call(self, skill, **kw):
        return self.skills.call(skill, kw)

    # -------------------------
    # find_large_files
    # -------------------------

    def test_find_large_files_top2(self):
        """最大的 2 个文件应为 big.bin(100) 与 c.txt(20)"""
        result = self._call("find_large_files", parent=self.test_dir, top=2)
        self.assertTrue(result["success"])
        items = result["result"]
        self.assertEqual(len(items), 2)
        self.assertEqual(os.path.basename(items[0]["path"]), "big.bin")
        self.assertEqual(items[0]["size"], 100)
        self.assertGreaterEqual(items[0]["size"], items[1]["size"])

    def test_find_large_files_default_top(self):
        """默认 top=10 应返回全部 5 个文件"""
        result = self._call("find_large_files", parent=self.test_dir)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["result"]), 5)

    def test_find_large_files_missing_dir(self):
        """不存在的目录应失败"""
        result = self._call("find_large_files", parent="/no/such")
        self.assertFalse(result["success"])

    # -------------------------
    # find_recent_files
    # -------------------------

    def test_find_recent_files(self):
        """显式设置文件修改时间后，按 days 过滤正确"""
        # 把所有文件 mtime 设到 3 天前
        old = time.time() - 3 * 86400
        for p in self._walk_paths(self.test_dir):
            os.utime(p, (old, old))

        # days=7：3 天前在 7 天窗口内 -> 全部 5 个
        result = self._call("find_recent_files", parent=self.test_dir, days=7)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["result"]), 5)

        # days=1：窗口仅覆盖最近 1 天，3 天前不在窗口内 -> 0 个
        result = self._call("find_recent_files", parent=self.test_dir, days=1)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["result"]), 0)

    def _walk_paths(self, root):
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                yield os.path.join(dirpath, name)

    # -------------------------
    # summarize_files
    # -------------------------

    def test_summarize_files(self):
        """统计应正确：5 文件、1 子目录、总大小、扩展名分布"""
        result = self._call("summarize_files", parent=self.test_dir)
        self.assertTrue(result["success"])
        stats = result["result"]
        self.assertEqual(stats["file_count"], 5)
        self.assertEqual(stats["dir_count"], 1)  # 仅 sub（test_dir 本身不算）
        self.assertEqual(stats["total_size"], 10 + 5 + 100 + 20 + 1)
        self.assertEqual(stats["ext_stats"][".txt"], 2)
        self.assertEqual(stats["ext_stats"][".tmp"], 2)
        self.assertEqual(stats["ext_stats"][".bin"], 1)

    def test_summarize_files_missing(self):
        """不存在的目录应失败"""
        result = self._call("summarize_files", parent="/no/such")
        self.assertFalse(result["success"])

    # -------------------------
    # cleanup_by_type
    # -------------------------

    def test_cleanup_by_type(self):
        """清理 .tmp 应删除全部 2 个 tmp 文件"""
        result = self._call("cleanup_by_type", parent=self.test_dir, extensions=(".tmp",))
        self.assertTrue(result["success"])
        removed = result["result"]
        self.assertEqual(len(removed), 2)
        # 确认已从磁盘删除
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "b.tmp")))
        self.assertFalse(os.path.exists(os.path.join(self.sub, "sub.tmp")))
        # 非 tmp 文件仍在
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "a.txt")))

    def test_cleanup_by_type_auto_dot(self):
        """传不带点的扩展名也应自动补 ."""
        result = self._call("cleanup_by_type", parent=self.test_dir, extensions=("tmp",))
        self.assertTrue(result["success"])
        self.assertEqual(len(result["result"]), 2)

    # -------------------------
    # batch_archive
    # -------------------------

    def test_batch_compress(self):
        """多个目录批量压缩为多个 zip"""
        d1 = os.path.join(self.test_dir, "d1")
        d2 = os.path.join(self.test_dir, "d2")
        os.makedirs(d1)
        os.makedirs(d2)
        open(os.path.join(d1, "x.txt"), "w").close()
        open(os.path.join(d2, "y.txt"), "w").close()

        out_dir = os.path.join(self.test_dir, "out")
        result = self._call("batch_archive", items=[d1, d2], dest_dir=out_dir,
                            action="compress")
        self.assertTrue(result["success"])
        mapping = result["result"]
        self.assertIn(d1, mapping)
        self.assertIn(d2, mapping)
        # 两个 zip 都应生成于 out 目录
        self.assertTrue(os.path.exists(mapping[d1]))
        self.assertTrue(os.path.exists(mapping[d2]))

    def test_batch_extract(self):
        """多个 zip 批量解压"""
        # 打包两个小目录
        from core.tool_registry import ToolRegistry
        reg = ToolRegistry()
        d1 = os.path.join(self.test_dir, "d1")
        d2 = os.path.join(self.test_dir, "d2")
        os.makedirs(d1)
        os.makedirs(d2)
        open(os.path.join(d1, "x.txt"), "w").close()
        open(os.path.join(d2, "y.txt"), "w").close()
        z1 = reg.call("manage_archive", {"action": "compress", "src_dir": d1,
                                         "dest_zip": os.path.join(self.test_dir, "a.zip")})
        z2 = reg.call("manage_archive", {"action": "compress", "src_dir": d2,
                                         "dest_zip": os.path.join(self.test_dir, "b.zip")})
        self.assertTrue(z1["success"] and z2["success"])

        out_dir = os.path.join(self.test_dir, "ex")
        result = self._call("batch_archive", items=[z1["result"], z2["result"]],
                            dest_dir=out_dir, action="extract")
        self.assertTrue(result["success"])
        mapping = result["result"]
        self.assertTrue(os.path.exists(os.path.join(mapping[z1["result"]], "x.txt")))
        self.assertTrue(os.path.exists(os.path.join(mapping[z2["result"]], "y.txt")))

    def test_batch_archive_invalid_action(self):
        """非法 action 应失败"""
        result = self._call("batch_archive", items=[], action="rotate")
        self.assertFalse(result["success"])

    def test_batch_extract_source_missing(self):
        """extract 源 zip 不存在应失败"""
        result = self._call("batch_archive", items=["/no/such.zip"], action="extract")
        self.assertFalse(result["success"])

    # -------------------------
    # duplicate_finder
    # -------------------------

    def test_duplicate_finder(self):
        """a.txt 与复制品大小相同应进同组；不同大小不应成组"""
        # 再造一个与 a.txt(10) 同大小的文件
        with open(os.path.join(self.test_dir, "small_copy.txt"), "wb") as f:
            f.write(b"q" * 10)
        result = self._call("duplicate_finder", parent=self.test_dir)
        self.assertTrue(result["success"])
        groups = result["result"]
        # 找到含 2 个 10 字节文件的一组（a.txt + small_copy.txt）
        sizes = [g[0]["size"] for g in groups if len(g) >= 2]
        self.assertIn(10, sizes)

    # -------------------------
    # rebuild_index / query_index
    # -------------------------

    def test_rebuild_index_writes_json(self):
        """rebuild_index 应落盘 JSON 索引并返回计数"""
        out = os.path.join(self.test_dir, "idx.json")
        result = self._call("rebuild_index", parent=self.test_dir, path=out)
        self.assertTrue(result["success"])
        meta = result["result"]
        # 6 项 = 4 文件(根) + sub 目录 + sub/c.txt + sub/sub.tmp
        self.assertEqual(meta["count"], 6)
        self.assertTrue(os.path.exists(out))
        # 验证 JSON 可解析
        with open(out, "r", encoding="utf-8") as f:
            entries = json.load(f)
        self.assertEqual(len(entries), 6)

    def test_rebuild_index_default_path(self):
        """未指定 path 时默认生成 {父目录}/{目录名}_index.json"""
        result = self._call("rebuild_index", parent=os.path.join(self.test_dir, "sub"))
        self.assertTrue(result["success"])
        self.assertTrue(result["result"]["index_path"].endswith("_index.json"))

    def test_query_index_matches_name(self):
        """query_index 应从索引中按名字模糊匹配"""
        out = os.path.join(self.test_dir, "idx.json")
        self._call("rebuild_index", parent=self.test_dir, path=out)
        result = self._call("query_index", path=out, keyword=".tmp")
        self.assertTrue(result["success"])
        names = {e["name"] for e in result["result"]}
        self.assertEqual(names, {"b.tmp", "sub.tmp"})

    def test_query_index_missing_file(self):
        """索引文件不存在应失败"""
        result = self._call("query_index", path="/no/idx.json", keyword="x")
        self.assertFalse(result["success"])


class TestSearchModes(unittest.TestCase):
    """search_file / query_index 的匹配模式测试（substring/exact/wildcard/regex）"""

    def setUp(self):
        self.skills = SkillLibrary()
        self.test_dir = tempfile.mkdtemp()
        # 文件集：report.txt, monthly_report.pdf, report1.log, README.md, data.csv
        for name in ("report.txt", "monthly_report.pdf", "report1.log",
                     "README.md", "data.csv"):
            open(os.path.join(self.test_dir, name), "w").close()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _search(self, keyword, mode=None):
        kw = {"directory": self.test_dir, "keyword": keyword}
        if mode:
            kw["mode"] = mode
        result = self.skills.call("search_file", kw)
        if result.get("success"):
            mode_shown = mode or "substring"
            names = sorted(self._basenames(result))
            print(f"  [search] mode={mode_shown:<10} keyword={keyword!r:>14} "
                  f"-> {len(names)} match: {names}")
        return result

    def _basenames(self, result):
        return {os.path.basename(p) for p in result["result"]}

    # ---- substring（默认，保持向后兼容） ----

    def test_substring_default(self):
        """默认 substring：包含即命中"""
        result = self._search("report")
        self.assertTrue(result["success"])
        self.assertEqual(self._basenames(result), {"report.txt", "monthly_report.pdf", "report1.log"})

    # ---- exact ----

    def test_exact_match(self):
        """exact：完整相等（不区分大小写）"""
        result = self._search("REPORT.TXT", mode="exact")
        self.assertTrue(result["success"])
        self.assertEqual(self._basenames(result), {"report.txt"})

    def test_exact_no_partial(self):
        """exact：不含子串命中"""
        result = self._search("report", mode="exact")
        self.assertTrue(result["success"])
        self.assertEqual(self._basenames(result), set())

    # ---- wildcard ----

    def test_wildcard_star(self):
        """wildcard：* 匹配任意"""
        result = self._search("*.pdf", mode="wildcard")
        self.assertTrue(result["success"])
        self.assertEqual(self._basenames(result), {"monthly_report.pdf"})

    def test_wildcard_question(self):
        """wildcard：? 匹配单字符"""
        result = self._search("report?.log", mode="wildcard")
        self.assertTrue(result["success"])
        self.assertEqual(self._basenames(result), {"report1.log"})

    # ---- regex ----

    def test_regex_match(self):
        """regex：正则表达式匹配"""
        result = self._search(r"report\d\.log", mode="regex")
        self.assertTrue(result["success"])
        self.assertEqual(self._basenames(result), {"report1.log"})

    def test_regex_ignore_case(self):
        """regex：不区分大小写"""
        result = self._search(r"readme", mode="regex")
        self.assertTrue(result["success"])
        self.assertEqual(self._basenames(result), {"README.md"})

    # ---- 非法模式 ----

    def test_invalid_mode(self):
        """非法 mode 应失败"""
        result = self._search("x", mode="banana")
        self.assertFalse(result["success"])
        self.assertIn("非法匹配模式", result["error"])

    # ---- 错误路径 ----

    def test_search_missing_dir(self):
        """搜索不存在的目录应失败"""
        result = self.skills.call("search_file", {"directory": "/no/such", "keyword": "x"})
        self.assertFalse(result["success"])

    # ---- query_index 模式 ----

    def test_query_index_modes(self):
        """query_index 支持精确/通配符/正则模式"""
        idx = os.path.join(self.test_dir, "idx.json")
        self.skills.call("rebuild_index", {"parent": self.test_dir, "path": idx})

        def query(mode, kw):
            r = self.skills.call("query_index", {"path": idx, "keyword": kw, "mode": mode})
            names = sorted(e["name"] for e in r["result"])
            print(f"  [query ] mode={mode:<10} keyword={kw!r:>12} "
                  f"-> {len(names)} match: {names}")
            return set(names)

        self.assertEqual(query("exact", "REPORT.TXT"), {"report.txt"})
        self.assertEqual(query("wildcard", "*report*"),
                         {"monthly_report.pdf", "report1.log", "report.txt"})
        self.assertEqual(query("regex", r"report\d\.log"), {"report1.log"})


class TestAgentSkills(unittest.TestCase):
    """组合型/安全类技能测试（回收站/备份/去重/整理）"""

    def setUp(self):
        self.skills = SkillLibrary()
        self.test_dir = tempfile.mkdtemp()
        self.f1 = os.path.join(self.test_dir, "doc.txt")
        with open(self.f1, "w") as f:
            f.write("important content")
        self.trash = Path.home() / ".reasonix_trash"

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)
        # 清理测试产生的回收站目录，避免污染真实 home
        if self.trash.exists():
            shutil.rmtree(self.trash, ignore_errors=True)

    def _call(self, skill, **kw):
        return self.skills.call(skill, kw)

    # ---- 回收站 ----

    def test_trash_and_restore(self):
        """trash_file 移入回收站，restore_file 恢复"""
        # 删除
        r = self._call("trash_file", path=self.f1)
        self.assertTrue(r["success"])
        self.assertFalse(os.path.exists(self.f1))
        trashed = r["result"]["trashed_path"]
        self.assertTrue(os.path.exists(trashed))

        # 恢复（用回收站内路径）
        r2 = self._call("restore_file", path=trashed)
        self.assertTrue(r2["success"])
        self.assertTrue(os.path.exists(self.f1))
        # 恢复后原路径再次恢复应失败（已不在回收站）
        r3 = self._call("restore_file", path=self.f1)
        self.assertFalse(r3["success"])

    def test_empty_trash_requires_confirm(self):
        """empty_trash 未确认应失败"""
        result = self._call("empty_trash", confirm=False)
        self.assertFalse(result["success"])

    def test_empty_trash_with_confirm(self):
        """empty_trash 确认后成功"""
        self._call("trash_file", path=self.f1)
        result = self._call("empty_trash", confirm=True)
        self.assertTrue(result["success"])

    # ---- 安全删除 ----

    def test_safe_delete_preview(self):
        """safe_delete 未确认时返回预览、不删除"""
        result = self._call("safe_delete", path=self.f1)
        self.assertTrue(result["success"])
        self.assertTrue(result["result"]["requires_confirmation"])
        self.assertTrue(os.path.exists(self.f1))

    def test_safe_delete_confirm(self):
        """safe_delete 确认后移入回收站"""
        result = self._call("safe_delete", path=self.f1, confirm=True)
        self.assertTrue(result["success"])
        self.assertFalse(os.path.exists(self.f1))

    # ---- 去重 ----

    def test_deduplicate(self):
        """deduplicate_files 预览识别重复并按哈希去重"""
        dup = os.path.join(self.test_dir, "dup.txt")
        with open(dup, "w") as f:
            f.write("important content")  # 与 f1 内容相同
        result = self._call("deduplicate_files", directory=self.test_dir)
        self.assertTrue(result["success"])
        # 预览：2 个同内容文件 -> 1 个 removed（未真正移除非 confirm）
        self.assertEqual(result["result"]["groups"], 1)
        self.assertEqual(len(result["result"]["removed"]), 1)

    # ---- 备份 ----

    def test_backup_and_restore(self):
        """backup_file 生成 .bak，restore_backup 恢复"""
        b = self._call("backup_file", path=self.f1)
        self.assertTrue(b["success"])
        self.assertTrue(os.path.exists(b["result"]))
        self.assertNotEqual(b["result"], self.f1)

        orig = self.f1
        os.remove(orig)
        r = self._call("restore_backup", backup_path=b["result"])
        self.assertTrue(r["success"])
        self.assertTrue(os.path.exists(orig))

    # ---- 智能整理 ----

    def test_smart_organize_by_type(self):
        """smart_organize 按类型整理"""
        img = os.path.join(self.test_dir, "photo.png")
        open(img, "wb").write(b"\x89PNG\r\n\x1a\n")
        result = self._call("smart_organize", directory=self.test_dir)
        self.assertTrue(result["success"])
        report = result["result"]["report"]
        self.assertIn("图片", report)
        self.assertTrue(os.path.exists(os.path.join(self.test_dir, "图片", "photo.png")))

    def test_smart_organize_by_date(self):
        """smart_organize 按年月整理"""
        result = self._call("smart_organize", directory=self.test_dir, by="date")
        self.assertTrue(result["success"])
        report = result["result"]["report"]
        self.assertTrue(report)  # 至少一个年份-月份子目录


if __name__ == "__main__":
    unittest.main()
