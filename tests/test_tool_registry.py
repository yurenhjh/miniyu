"""
test_tool_registry.py
第4组：ToolRegistry 单元测试
覆盖注册表管理 + 全部工具 + sort_directory / index_files / manage_archive
"""

import json
import os
import shutil
import tempfile
import unittest
import zipfile

from core.tool_registry import ToolRegistry


class TestToolRegistry(unittest.TestCase):

    def setUp(self):
        self.registry = ToolRegistry()
        self.test_dir = tempfile.mkdtemp()
        self.file1 = os.path.join(self.test_dir, "a.txt")
        self.file2 = os.path.join(self.test_dir, "b.txt")
        with open(self.file1, "w", encoding="utf-8") as f:
            f.write("Hello Group4")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------
    # 注册表管理
    # -------------------------

    def test_list_tools(self):
        """测试工具列表包含核心工具"""
        tools = self.registry.list_tools()
        self.assertIn("copy_file", tools)
        self.assertIn("move_path", tools)
        self.assertIn("run_command", tools)
        self.assertIn("sort_directory", tools)
        self.assertIn("index_files", tools)
        self.assertIn("manage_archive", tools)
        self.assertIn("get_path_metadata", tools)
        self.assertIn("file_checksum", tools)
        self.assertIn("disk_usage", tools)
        self.assertIn("browser_launch", tools)
        self.assertIn("browser_snapshot", tools)
        self.assertIn("click_at", tools)
        self.assertEqual(len(tools), 57)

    def test_unregister_tool(self):
        """测试注销工具"""
        self.registry.unregister("copy_file")
        self.assertNotIn("copy_file", self.registry.list_tools())

    def test_register_new_tool(self):
        """测试动态注册新工具"""
        def my_tool():
            return "custom"
        self.registry.register("my_tool", my_tool)
        self.assertIn("my_tool", self.registry.list_tools())
        result = self.registry.call("my_tool")
        self.assertEqual(result["result"], "custom")

    # -------------------------
    # 文件操作
    # -------------------------

    def test_copy_file(self):
        """测试复制文件"""
        result = self.registry.call("copy_file", {
            "src": self.file1,
            "dest": self.file2
        })
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(self.file2))

    def test_move_file(self):
        """测试移动文件"""
        result = self.registry.call("move_path", {
            "src": self.file1,
            "dest": self.file2
        })
        self.assertTrue(result["success"])
        self.assertFalse(os.path.exists(self.file1))
        self.assertTrue(os.path.exists(self.file2))

    def test_rename_file(self):
        """测试重命名文件"""
        result = self.registry.call("rename_path", {
            "src": self.file1,
            "dest": self.file2
        })
        self.assertTrue(result["success"])
        self.assertFalse(os.path.exists(self.file1))
        self.assertTrue(os.path.exists(self.file2))

    def test_delete_file(self):
        """测试删除文件"""
        result = self.registry.call("delete_file", {"path": self.file1})
        self.assertTrue(result["success"])
        self.assertFalse(os.path.exists(self.file1))

    # -------------------------
    # 文件夹操作
    # -------------------------

    def test_create_folder(self):
        """测试创建目录"""
        folder = os.path.join(self.test_dir, "demo")
        result = self.registry.call("create_directory", {"path": folder})
        self.assertTrue(result["success"])
        self.assertTrue(os.path.isdir(folder))

    def test_delete_folder(self):
        """测试删除目录"""
        folder = os.path.join(self.test_dir, "todelete")
        os.makedirs(folder)
        result = self.registry.call("delete_directory", {"path": folder})
        self.assertTrue(result["success"])
        self.assertFalse(os.path.exists(folder))

    def test_list_directory(self):
        """测试列出目录内容"""
        result = self.registry.call("list_directory", {"path": self.test_dir})
        self.assertTrue(result["success"])
        self.assertIsInstance(result["result"], list)

    # -------------------------
    # 文件查询
    # -------------------------

    def test_file_exists(self):
        """测试文件存在检查"""
        result = self.registry.call("file_exists", {"path": self.file1})
        self.assertTrue(result["result"])

    def test_file_not_exists(self):
        """测试文件不存在"""
        result = self.registry.call("file_exists", {"path": "/nonexistent"})
        self.assertFalse(result["result"])

    def test_get_file_size(self):
        """测试获取文件大小"""
        result = self.registry.call("get_file_size", {"path": self.file1})
        self.assertTrue(result["result"] > 0)

    # -------------------------
    # 文本文件
    # -------------------------

    def test_write_and_read(self):
        """测试写入和读取文本文件"""
        path = os.path.join(self.test_dir, "hello.txt")
        self.registry.call("write_text_file", {
            "path": path,
            "content": "Python Test"
        })
        result = self.registry.call("read_text_file", {"path": path})
        self.assertEqual(result["result"], "Python Test")

    # -------------------------
    # 系统工具
    # -------------------------

    def test_current_directory(self):
        """测试获取当前路径"""
        result = self.registry.call("current_directory", {})
        self.assertTrue(result["success"])
        self.assertIsInstance(result["result"], str)

    def test_run_command(self):
        """测试执行系统命令"""
        result = self.registry.call("run_command", {"cmd": "echo hello"})
        self.assertTrue(result["success"])
        self.assertIn("hello", result["result"]["stdout"])

    # -------------------------
    # 异常处理
    # -------------------------

    def test_invalid_tool(self):
        """测试调用未注册工具"""
        result = self.registry.call("abcdefg", {})
        self.assertFalse(result["success"])
        self.assertIn("未注册", result["error"])

    def test_missing_params(self):
        """测试缺少参数"""
        result = self.registry.call("copy_file", {})
        self.assertFalse(result["success"])

    # -------------------------
    # 调用统计
    # -------------------------

    def test_get_stats_empty(self):
        """测试空统计"""
        stats = self.registry.get_stats()
        self.assertEqual(stats["total_calls"], 0)
        self.assertEqual(stats["by_tool"], {})

    def test_get_stats_after_calls(self):
        """测试调用后统计"""
        self.registry.call("current_directory", {})
        self.registry.call("current_directory", {})
        self.registry.call("file_exists", {"path": self.file1})

        stats = self.registry.get_stats()
        self.assertEqual(stats["total_calls"], 3)
        self.assertEqual(stats["by_tool"]["current_directory"], 2)
        self.assertEqual(stats["by_tool"]["file_exists"], 1)
        self.assertIn("most_used", stats)
        self.assertGreaterEqual(stats["success_rate"], 0)

    def test_clear_logs(self):
        """测试清空日志"""
        self.registry.call("current_directory", {})
        self.assertEqual(self.registry.get_stats()["total_calls"], 1)
        self.registry.clear_logs()
        self.assertEqual(self.registry.get_stats()["total_calls"], 0)


class TestSortDirectory(unittest.TestCase):
    """sort_directory 工具单元测试"""

    def setUp(self):
        self.registry = ToolRegistry()
        self.test_dir = tempfile.mkdtemp()

        # 准备一批文件与目录，便于验证不同排序键
        self.files = {
            "a_small.txt": 10,
            "b_big.txt": 500,
            "c_mid.txt": 200,
        }
        for name, size in self.files.items():
            path = os.path.join(self.test_dir, name)
            with open(path, "wb") as f:
                f.write(b"x" * size)

        # 目录（无 size，mtime 有效）
        self.folder = os.path.join(self.test_dir, "zz_some_folder")
        os.makedirs(self.folder)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _call(self, **kw):
        return self.registry.call("sort_directory", {"path": self.test_dir, **kw})

    # -------------------------
    # 基本返回结构
    # -------------------------

    def test_success_result_shape(self):
        """成功调用返回 success 与 skill 路由等关键字段"""
        result = self._call()
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "sort_directory")
        self.assertIsInstance(result["result"], list)

    def test_each_entry_has_four_constant_fields(self):
        """每个条目恒定包含 name/type/size/mtime 四字段"""
        items = self._call()["result"]
        for e in items:
            self.assertEqual(
                set(e.keys()), {"name", "type", "size", "mtime"}
            )

    def test_directory_size_is_none(self):
        """目录的 size 应为 None，文件的 size 为字节数"""
        items = self._call()["result"]
        folder = next(e for e in items if e["type"] == "directory")
        self.assertIsNone(folder["size"])
        file = next(e for e in items if e["type"] == "file")
        self.assertGreater(file["size"], 0)

    # -------------------------
    # 目录优先 + name 排序
    # -------------------------

    def test_directory_always_first_by_default(self):
        """默认（name 升序）目录应排在文件前面"""
        items = self._call()["result"]
        self.assertEqual(items[0]["type"], "directory")
        self.assertEqual(items[0]["name"], "zz_some_folder")

    def test_directory_first_even_when_reverse(self):
        """即使 reverse=True，目录仍排最前（不受 reverse 影响）"""
        items = self._call(sort_by="name", reverse=True)["result"]
        self.assertEqual(items[0]["type"], "directory")

    def test_files_sorted_by_name_ascending(self):
        """name 升序：文件按字典序排"""
        names = [e["name"] for e in self._call()["result"] if e["type"] == "file"]
        self.assertEqual(names, ["a_small.txt", "b_big.txt", "c_mid.txt"])

    def test_files_sorted_by_name_descending(self):
        """name 降序：文件按字典序倒排"""
        names = [e["name"] for e in self._call(sort_by="name", reverse=True)["result"] if e["type"] == "file"]
        self.assertEqual(names, ["c_mid.txt", "b_big.txt", "a_small.txt"])

    # -------------------------
    # size / mtime 排序
    # -------------------------

    def test_sort_by_size_ascending(self):
        """按大小升序：文件名顺序 a(10) c(200) b(500)"""
        names = [e["name"] for e in self._call(sort_by="size")["result"] if e["type"] == "file"]
        self.assertEqual(names, ["a_small.txt", "c_mid.txt", "b_big.txt"])

    def test_sort_by_size_descending(self):
        """按大小降序：文件名顺序 b(500) c(200) a(10)"""
        names = [e["name"] for e in self._call(sort_by="size", reverse=True)["result"] if e["type"] == "file"]
        self.assertEqual(names, ["b_big.txt", "c_mid.txt", "a_small.txt"])

    def test_sort_by_mtime_returns_stable(self):
        """mtime 排序可用：# 所有文件时间戳相同，应按 name 恒升序兜底"""
        items = self._call(sort_by="mtime")["result"]
        file_names = [e["name"] for e in items if e["type"] == "file"]
        self.assertEqual(file_names, ["a_small.txt", "b_big.txt", "c_mid.txt"])

    def test_sort_by_size_with_multiple_directories(self):
        """多个目录（size 均为 None）时按 size 排序不应崩溃，且目录仍最先"""
        os.makedirs(os.path.join(self.test_dir, "aa_another_folder"), exist_ok=True)
        for sort_by in ("size", "mtime"):
            result = self._call(sort_by=sort_by, reverse=True)
            self.assertTrue(result["success"], sort_by)
            items = result["result"]
            # 前两名为目录（两个目录都在前）
            self.assertEqual(items[0]["type"], "directory")
            self.assertEqual(items[1]["type"], "directory")
            # 目录间 size 为 None：应回退 name 恒升序兜底
            dir_names = [e["name"] for e in items if e["type"] == "directory"]
            self.assertEqual(dir_names, ["aa_another_folder", "zz_some_folder"])

    # -------------------------
    # 决策性 / 确定性
    # -------------------------

    def test_tie_broken_by_name(self):
        """主键相等时按 name 恒升序兜底（确定性）"""
        r1 = self._call(sort_by="mtime")
        r2 = self._call(sort_by="mtime")
        self.assertEqual(r1["result"], r2["result"])

    # -------------------------
    # 空目录
    # -------------------------

    def test_empty_directory(self):
        """空目录返回空列表"""
        empty = tempfile.mkdtemp()
        try:
            result = self.registry.call("sort_directory", {"path": empty})
            self.assertTrue(result["success"])
            self.assertEqual(result["result"], [])
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    # -------------------------
    # 错误路径
    # -------------------------

    def test_nonexistent_path(self):
        """不存在的路径应失败"""
        result = self.registry.call(
            "sort_directory", {"path": "/nonexistent_path_xyz_123"}
        )
        self.assertFalse(result["success"])

    def test_path_is_file_should_fail(self):
        """对文件（非目录）排序应失败"""
        result = self.registry.call(
            "sort_directory", {"path": os.path.join(self.test_dir, "a_small.txt")}
        )
        self.assertFalse(result["success"])

    def test_invalid_sort_by(self):
        """非法排序键应失败"""
        result = self.registry.call(
            "sort_directory", {"path": self.test_dir, "sort_by": "banana"}
        )
        self.assertFalse(result["success"])
        self.assertIn("非法排序键", result["error"])


class TestIndexFiles(unittest.TestCase):
    """index_files 工具单元测试"""

    def setUp(self):
        self.registry = ToolRegistry()
        self.test_dir = tempfile.mkdtemp()

        # 根目录两个文件 + 一个子目录（内含一个文件）
        with open(os.path.join(self.test_dir, "a.txt"), "w") as f:
            f.write("hello")
        sub = os.path.join(self.test_dir, "sub")
        os.makedirs(sub)
        with open(os.path.join(sub, "b.txt"), "w") as f:
            f.write("world")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _index(self, **kw):
        kw = {"path": self.test_dir, **kw}
        return self.registry.call("index_files", kw)

    # -------------------------
    # 基本构建
    # -------------------------

    def test_success_result_shape(self):
        """成功调用返回 success 与 tool 字段"""
        result = self._index()
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "index_files")

    def test_recursive_collects_all(self):
        """递归模式应收集根目录文件与子目录文件"""
        items = self._index()["result"]
        names = {e["name"] for e in items}
        self.assertEqual(names, {"a.txt", "b.txt", "sub"})

    def test_abs_path(self):
        """条目 path 应为绝对路径"""
        items = self._index()["result"]
        for e in items:
            self.assertTrue(os.path.isabs(e["path"]))

    def test_entry_structure(self):
        """每个条目恒定包含 path/name/type/size/mtime 五字段"""
        items = self._index()["result"]
        for e in items:
            self.assertEqual(
                set(e.keys()), {"path", "name", "type", "size", "mtime"}
            )

    def test_dir_size_none(self):
        """目录条目 size 应为 None，文件 size 非 None"""
        items = self._index()["result"]
        d = next(e for e in items if e["type"] == "directory")
        self.assertIsNone(d["size"])

    # -------------------------
    # 非递归
    # -------------------------

    def test_non_recursive_only_root(self):
        """非递归模式只含根目录直接子项（不含子目录内文件）"""
        items = self._index(recursive=False)["result"]
        names = {e["name"] for e in items}
        self.assertEqual(names, {"a.txt", "sub"})

    # -------------------------
    # 空目录
    # -------------------------

    def test_empty_directory(self):
        """空目录返回空列表"""
        empty = tempfile.mkdtemp()
        try:
            result = self.registry.call("index_files", {"path": empty})
            self.assertTrue(result["success"])
            self.assertEqual(result["result"], [])
        finally:
            shutil.rmtree(empty, ignore_errors=True)

    # -------------------------
    # 错误路径
    # -------------------------

    def test_nonexistent_path(self):
        """不存在的路径应失败"""
        result = self.registry.call(
            "index_files", {"path": "/nonexistent_index_xyz"}
        )
        self.assertFalse(result["success"])

    def test_path_is_file_should_fail(self):
        """对文件（非目录）建索引应失败"""
        result = self.registry.call(
            "index_files", {"path": os.path.join(self.test_dir, "a.txt")}
        )
        self.assertFalse(result["success"])

    # -------------------------
    # 持久化 / 加载
    # -------------------------

    def test_persist_writes_json(self):
        """persist 应把索引写为 JSON 文件，内容与返回一致"""
        out = os.path.join(self.test_dir, "index.json")
        result = self._index(persist=out)
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(out))
        with open(out, "r", encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(saved, result["result"])

    def test_load_reads_saved_index(self):
        """load 应读取已保存索引，且不依赖真实文件系统遍历"""
        out = os.path.join(self.test_dir, "index.json")
        self._index(persist=out)

        # 删掉真实目录文件区，只保留索引文件本身，验证 load 仍返回原索引
        for name in ("a.txt", "sub"):
            p = os.path.join(self.test_dir, name)
            if os.path.isfile(p):
                os.unlink(p)
            elif os.path.isdir(p):
                shutil.rmtree(p)

        result = self.registry.call("index_files", {"path": self.test_dir, "load": out})
        self.assertTrue(result["success"])
        items = result["result"]
        names = {e["name"] for e in items}
        self.assertEqual(names, {"a.txt", "b.txt", "sub"})

    def test_load_missing_file_fails(self):
        """load 不存在的文件应失败"""
        result = self.registry.call(
            "index_files", {"path": self.test_dir, "load": "/no/such/index.json"}
        )
        self.assertFalse(result["success"])


class TestManageArchive(unittest.TestCase):
    """manage_archive 工具单元测试"""

    def setUp(self):
        self.registry = ToolRegistry()
        self.test_dir = tempfile.mkdtemp()
        self.src = os.path.join(self.test_dir, "src")
        os.makedirs(os.path.join(self.src, "sub"))
        with open(os.path.join(self.src, "a.txt"), "w") as f:
            f.write("hello")
        with open(os.path.join(self.src, "sub", "b.txt"), "w") as f:
            f.write("world")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _call(self, **kw):
        return self.registry.call("manage_archive", kw)

    # -------------------------
    # 压缩
    # -------------------------

    def test_compress_success_shape(self):
        """压缩成功返回 success 与 tool 字段，result 为 zip 路径"""
        out = os.path.join(self.test_dir, "out.zip")
        result = self._call(action="compress", src_dir=self.src, dest_zip=out)
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "manage_archive")
        self.assertTrue(result["result"].endswith(".zip"))

    def test_compress_default_action(self):
        """默认 action 即 compress"""
        out = os.path.join(self.test_dir, "out.zip")
        result = self._call(src_dir=self.src, dest_zip=out)
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(out))

    def test_compress_default_name(self):
        """未指定 dest 时生成 {目录名}_archive.zip"""
        result = self._call(action="compress", src_dir=self.src)
        self.assertTrue(result["success"])
        expected = os.path.join(self.test_dir, "src_archive.zip")
        self.assertEqual(result["result"], os.path.abspath(expected))
        self.assertTrue(os.path.exists(expected))

    def test_compress_extrapolate_suffix(self):
        """dest 不带 .zip 自动补"""
        out = os.path.join(self.test_dir, "out")
        result = self._call(action="compress", src_dir=self.src, dest_zip=out)
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(out + ".zip"))

    def test_compress_zips_relative_paths(self):
        """zip 内路径相对源目录根，含子目录"""
        out = os.path.join(self.test_dir, "out.zip")
        self._call(action="compress", src_dir=self.src, dest_zip=out)
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        self.assertIn("a.txt", names)
        self.assertIn("sub/b.txt", names)

    # -------------------------
    # 解压
    # -------------------------

    def test_extract_default_dir(self):
        """解压默认到 zip 同级的 {zip名}_extracted 目录"""
        out = os.path.join(self.test_dir, "out.zip")
        self._call(action="compress", src_dir=self.src, dest_zip=out)

        result = self._call(action="extract", dest_zip=out)
        self.assertTrue(result["success"])
        extracted = os.path.join(self.test_dir, "out_extracted")
        self.assertEqual(result["result"], os.path.abspath(extracted))
        self.assertTrue(os.path.exists(os.path.join(extracted, "a.txt")))
        self.assertTrue(os.path.exists(os.path.join(extracted, "sub", "b.txt")))

    def test_extract_to_custom_dir(self):
        """extract_to 指定解压目标目录"""
        out = os.path.join(self.test_dir, "out.zip")
        self._call(action="compress", src_dir=self.src, dest_zip=out)
        target = os.path.join(self.test_dir, "custom_out")

        result = self._call(action="extract", dest_zip=out, extract_to=target)
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(os.path.join(target, "a.txt")))

    def test_extract_existing_target_fails(self):
        """解压目标已存在应报错（防覆盖）"""
        out = os.path.join(self.test_dir, "out.zip")
        self._call(action="compress", src_dir=self.src, dest_zip=out)
        target = os.path.join(self.test_dir, "out_extracted")
        os.makedirs(target)  # 提前创建 -> 应失败

        result = self._call(action="extract", dest_zip=out)
        self.assertFalse(result["success"])

    def test_extract_missing_zip_fails(self):
        """解压不存在的 zip 应失败"""
        result = self._call(action="extract", dest_zip="/no/such/file.zip")
        self.assertFalse(result["success"])

    # -------------------------
    # 错误路径
    # -------------------------

    def test_invalid_action(self):
        """非法 action 应失败"""
        result = self._call(action="rotate", src_dir=self.src)
        self.assertFalse(result["success"])
        self.assertIn("非法 action", result["error"])

    def test_compress_nonexistent_src(self):
        """压缩源目录不存在应失败"""
        result = self._call(action="compress", src_dir="/nonexistent_src_xyz")
        self.assertFalse(result["success"])

    def test_compress_src_is_file_should_fail(self):
        """压缩源是文件（非目录）应失败"""
        result = self._call(
            action="compress", src_dir=os.path.join(self.src, "a.txt"))
        self.assertFalse(result["success"])


class TestNewTools(unittest.TestCase):
    """新增工具单元测试（元数据/完整性/批量/磁盘/查找/文件创建）"""

    def setUp(self):
        self.registry = ToolRegistry()
        self.test_dir = tempfile.mkdtemp()
        self.f1 = os.path.join(self.test_dir, "a.txt")
        with open(self.f1, "w") as f:
            f.write("hello world")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_hidden_aliases(self):
        """旧名可调用（归一化为规范名），但 list_tools 不显示别名"""
        for alias, canonical in {
            "move_file": "move_path",
            "rename_file": "rename_path",
            "create_folder": "create_directory",
            "delete_folder": "delete_directory",
            "get_file_metadata": "get_path_metadata",
            "get_folder_metadata": "get_directory_metadata",
        }.items():
            self.assertNotIn(alias, self.registry.list_tools())
            self.assertEqual(self.registry.resolve_tool(alias), canonical)

    def test_call_by_alias_returns_canonical(self):
        """通过别名调用返回工具字段为规范名"""
        result = self.registry.call("get_file_metadata", {"path": self.f1})
        self.assertTrue(result["success"])
        self.assertEqual(result["tool"], "get_path_metadata")

    def test_error_codes(self):
        """错误路径带 error_code（与第3组契约一致）"""
        # 未注册工具
        nf = self.registry.call("zzz_tool", {})
        self.assertFalse(nf["success"])
        self.assertEqual(nf["error_code"], "TOOL_NOT_FOUND")
        # 调用异常工具（参数/路径错误）
        err = self.registry.call("get_file_size", {"path": "/no/such/file"})
        self.assertFalse(err["success"])
        self.assertEqual(err["error_code"], "TOOL_ERROR")

    def _call(self, name, **kw):
        return self.registry.call(name, kw)

    # ---- create_file ----

    def test_create_file(self):
        """create_file 创建空文件"""
        p = os.path.join(self.test_dir, "new.txt")
        result = self._call("create_file", path=p)
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(p))
        self.assertTrue(os.path.isabs(result["result"]))

    def test_create_file_already_exists(self):
        """create_file 对已存在文件不报错"""
        result = self._call("create_file", path=self.f1)
        self.assertTrue(result["success"])

    # ---- get_file_metadata ----

    def test_get_file_metadata(self):
        """get_file_metadata 返回完整字段"""
        result = self._call("get_path_metadata", path=self.f1)
        self.assertTrue(result["success"])
        md = result["result"]
        self.assertEqual(md["name"], "a.txt")
        self.assertEqual(md["extension"], ".txt")
        self.assertEqual(md["size"], 11)  # "hello world"
        self.assertFalse(md["is_hidden"])

    def test_get_file_metadata_missing(self):
        """metadata 不存在的文件应失败"""
        result = self._call("get_path_metadata", path="/no/such")
        self.assertFalse(result["success"])

    # ---- get_folder_metadata ----

    def test_get_folder_metadata(self):
        """get_folder_metadata 统计文件夹"""
        result = self._call("get_directory_metadata", path=self.test_dir)
        self.assertTrue(result["success"])
        md = result["result"]
        self.assertEqual(md["file_count"], 1)
        self.assertEqual(md["total_size"], 11)
        self.assertEqual(md["ext_stats"][".txt"], 1)

    # ---- check_file_access ----

    def test_check_file_access(self):
        """check_file_access 返回三权限字段"""
        result = self._call("check_file_access", path=self.f1)
        self.assertTrue(result["success"])
        a = result["result"]
        self.assertIn("readable", a)
        self.assertIn("writable", a)
        self.assertIn("executable", a)
        self.assertTrue(a["readable"])

    # ---- file_checksum ----

    def test_file_checksum_sha256(self):
        """file_checksum 计算 sha256"""
        result = self._call("file_checksum", path=self.f1)
        self.assertTrue(result["success"])
        c = result["result"]
        self.assertEqual(c["algorithm"], "sha256")
        self.assertEqual(len(c["checksum"]), 64)

    def test_file_checksum_md5_deterministic(self):
        """同一文件两次 md5 相同"""
        r1 = self._call("file_checksum", path=self.f1, algorithm="md5")
        r2 = self._call("file_checksum", path=self.f1, algorithm="md5")
        self.assertEqual(r1["result"]["checksum"], r2["result"]["checksum"])

    def test_file_checksum_invalid_algorithm(self):
        """非法算法应失败"""
        result = self._call("file_checksum", path=self.f1, algorithm="xx")
        self.assertFalse(result["success"])

    # ---- compare_files ----

    def test_compare_same(self):
        """compare_files 相同文件返回 same=True"""
        f2 = os.path.join(self.test_dir, "copy.txt")
        shutil.copy2(self.f1, f2)
        result = self._call("compare_files", path1=self.f1, path2=f2)
        self.assertTrue(result["success"])
        self.assertTrue(result["result"]["same"])

    def test_compare_diff(self):
        """compare_files 不同文件返回 diff"""
        f2 = os.path.join(self.test_dir, "b.txt")
        with open(f2, "w") as f:
            f.write("different")
        result = self._call("compare_files", path1=self.f1, path2=f2)
        self.assertTrue(result["success"])
        self.assertFalse(result["result"]["same"])
        self.assertGreater(len(result["result"]["diff"]), 0)

    # ---- batch_copy / batch_move ----

    def test_batch_copy(self):
        """batch_copy 复制多个文件"""
        f2 = os.path.join(self.test_dir, "b.txt")
        open(f2, "w").write("b")
        out = os.path.join(self.test_dir, "out")
        result = self._call("batch_copy", files=[self.f1, f2], dest_dir=out)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["result"]), 2)
        self.assertTrue(os.path.exists(os.path.join(out, "a.txt")))

    def test_batch_move(self):
        """batch_move 移动多个文件"""
        f2 = os.path.join(self.test_dir, "b.txt")
        open(f2, "w").write("b")
        out = os.path.join(self.test_dir, "out")
        result = self._call("batch_move", files=[self.f1, f2], dest_dir=out)
        self.assertTrue(result["success"])
        self.assertEqual(len(result["result"]), 2)
        self.assertFalse(os.path.exists(self.f1))

    # ---- disk / 查找 ----

    def test_disk_usage(self):
        """disk_usage 返回空间字段"""
        result = self._call("disk_usage", path=self.test_dir)
        self.assertTrue(result["success"])
        d = result["result"]
        self.assertIn("total", d)
        self.assertIn("used", d)
        self.assertIn("free", d)
        self.assertGreater(d["total"], 0)

    def test_directory_size(self):
        """directory_size 统计目录大小"""
        result = self._call("directory_size", path=self.test_dir)
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["file_count"], 1)
        self.assertEqual(result["result"]["size"], 11)

    def test_find_empty_files(self):
        """find_empty_files 找出空文件"""
        empty = os.path.join(self.test_dir, "empty.txt")
        open(empty, "w").close()
        result = self._call("find_empty_files", directory=self.test_dir)
        self.assertTrue(result["success"])
        self.assertIn(os.path.abspath(empty), result["result"])

    def test_find_empty_directories(self):
        """find_empty_directories 找出空目录"""
        edir = os.path.join(self.test_dir, "emptydir")
        os.makedirs(edir)
        result = self._call("find_empty_directories", directory=self.test_dir)
        self.assertTrue(result["success"])
        self.assertIn(os.path.abspath(edir), result["result"])

    def test_find_old_files(self):
        """find_old_files 找长期未修改文件（days=0 全部命中）"""
        result = self._call("find_old_files", directory=self.test_dir, days=0)
        self.assertTrue(result["success"])
        self.assertIn(os.path.abspath(self.f1), result["result"])

    # ---- 异常处理：同名冲突 / 创建语义 / 覆盖 ----

    def test_copy_overwrite_refused(self):
        """copy 到已存在 dest 默认报错"""
        f2 = os.path.join(self.test_dir, "b.txt")
        open(f2, "w").write("bbb")
        result = self._call("copy_file", src=self.f1, dest=f2)
        self.assertFalse(result["success"])
        # 目标内容未被覆盖
        with open(f2) as f:
            self.assertEqual(f.read(), "bbb")

    def test_copy_overwrite_allowed(self):
        """copy 到已存在 dest 且 overwrite=True 成功"""
        f2 = os.path.join(self.test_dir, "b.txt")
        open(f2, "w").write("old")
        result = self._call("copy_file", src=self.f1, dest=f2, overwrite=True)
        self.assertTrue(result["success"])
        with open(f2) as f:
            self.assertEqual(f.read(), "hello world")

    def test_rename_same_target_conflict(self):
        """rename 到已存在同名目标默认报错"""
        f2 = os.path.join(self.test_dir, "b.txt")
        open(f2, "w").write("bbb")
        result = self._call("rename_path", src=self.f1, dest=f2)
        self.assertFalse(result["success"])

    def test_move_conflict_default_refused(self):
        """move 到已存在同名目标默认报错"""
        f2 = os.path.join(self.test_dir, "b.txt")
        open(f2, "w").write("bbb")
        result = self._call("move_path", src=self.f1, dest=f2)
        self.assertFalse(result["success"])
        # 源文件未被动（未移动/未删除）
        self.assertTrue(os.path.exists(self.f1))

    def test_create_file_exists_error(self):
        """create_file 已存在且 exists=error 应报错"""
        result = self._call("create_file", path=self.f1, exists="error")
        self.assertFalse(result["success"])
        # 内容未被改动
        with open(self.f1) as f:
            self.assertEqual(f.read(), "hello world")

    def test_create_file_exists_overwrite(self):
        """create_file 已存在且 exists=overwrite 应清空"""
        result = self._call("create_file", path=self.f1, exists="overwrite")
        self.assertTrue(result["success"])
        self.assertEqual(os.path.getsize(self.f1), 0)

    def test_create_folder_exists_error(self):
        """create_folder 已存在且 exists=error 应报错"""
        result = self._call("create_directory", path=self.test_dir, exists="error")
        self.assertFalse(result["success"])

    def test_batch_copy_conflict_refused(self):
        """batch_copy 目标存在同名文件且未 overwrite 时整体报错、不复制"""
        out = os.path.join(self.test_dir, "out")
        os.makedirs(out)
        # 目标里已有同名 a.txt
        open(os.path.join(out, "a.txt"), "w").write("old")
        f2 = os.path.join(self.test_dir, "b.txt")
        open(f2, "w").write("bbb")
        result = self._call("batch_copy", files=[self.f1, f2], dest_dir=out)
        self.assertFalse(result["success"])
        # 预检查失败，b.txt 不应被复制过去
        self.assertFalse(os.path.exists(os.path.join(out, "b.txt")))

    def test_read_text_file_missing(self):
        """read 不存在的文本文件应报错"""
        result = self._call("read_text_file", path="/no/such/file.txt")
        self.assertFalse(result["success"])

    def test_read_text_file_max_bytes(self):
        """read_text_file 超过 max_bytes 应报错"""
        result = self._call("read_text_file", path=self.f1, max_bytes=5)
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
