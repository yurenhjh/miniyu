"""
mock_tools.py
第4组：Mock版 ToolRegistry（完整版）
提供全部系统工具的Mock实现，用于跨组联调
"""

from pathlib import Path


class MockToolRegistry:
    """Mock版工具注册表（57个工具全量Mock）"""

    def __init__(self):
        self.tools = {}
        self.aliases = {}
        self._register_defaults()

    def _register_defaults(self):
        """注册所有Mock工具"""

        # 文件操作
        self.register("copy_file", self._copy_file)
        self.register("move_path", self._move_path, aliases=("move_file",))
        self.register("delete_file", self._delete_file)
        self.register("rename_path", self._rename_path, aliases=("rename_file",))
        self.register("create_file", self._create_file)

        # 文件夹操作
        self.register("create_directory", self._create_directory, aliases=("create_folder",))
        self.register("delete_directory", self._delete_directory, aliases=("delete_folder",))
        self.register("list_directory", self._list_directory)
        self.register("sort_directory", self._sort_directory)
        self.register("index_files", self._index_files)
        self.register("manage_archive", self._manage_archive)

        # 文件信息
        self.register("file_exists", self._file_exists)
        self.register("get_file_size", self._get_file_size)
        self.register("get_path_metadata", self._get_path_metadata, aliases=("get_file_metadata",))
        self.register("get_directory_metadata", self._get_directory_metadata, aliases=("get_folder_metadata",))
        self.register("check_file_access", self._check_file_access)

        # 完整性 / 比较
        self.register("file_checksum", self._file_checksum)
        self.register("compare_files", self._compare_files)

        # 文本文件
        self.register("read_text_file", self._read_text_file)
        self.register("write_text_file", self._write_text_file)

        # 批量操作
        self.register("batch_copy", self._batch_copy)
        self.register("batch_move", self._batch_move)

        # 磁盘 / 查找
        self.register("disk_usage", self._disk_usage)
        self.register("directory_size", self._directory_size)
        self.register("find_empty_files", self._find_empty_files)
        self.register("find_empty_directories", self._find_empty_directories)
        self.register("find_old_files", self._find_old_files)
        self.register("find_large_files", self._find_large_files)
        self.register("search_files", self._search_files)
        self.register("search_in_files", self._search_in_files)
        self.register("edit_file", self._edit_file)

        # 图片
        self.register("get_image_metadata", self._get_image_metadata)
        self.register("rotate_image", self._rotate_image)

        # 系统工具
        self.register("current_directory", self._current_directory)
        self.register("run_command", self._run_command)

        # 应用操作
        self.register("list_windows", self._list_windows)
        self.register("find_window", self._find_window)
        self.register("activate_window", self._activate_window)
        self.register("send_hotkey", self._send_hotkey)
        self.register("send_text", self._send_text)
        self.register("take_screenshot", self._take_screenshot)
        self.register("click_at", self._click_at)

        # 浏览器结构化操作（基于 CDP）
        self.register("browser_launch", self._browser_launch)
        self.register("browser_close", self._browser_close)
        self.register("browser_navigate", self._browser_navigate)
        self.register("browser_snapshot", self._browser_snapshot)
        self.register("browser_click", self._browser_click)
        self.register("browser_type", self._browser_type)
        self.register("browser_read_text", self._browser_read_text)
        self.register("browser_screenshot", self._browser_screenshot)
        self.register("browser_wait", self._browser_wait)

        # 系统管理（进程 / 网络 / 环境变量）
        self.register("list_processes", self._list_processes)
        self.register("get_process_info", self._get_process_info)
        self.register("kill_process", self._kill_process)
        self.register("terminate_process", self._terminate_process)
        self.register("network_status", self._network_status)
        self.register("ping_host", self._ping_host)
        self.register("list_env_vars", self._list_env_vars)
        self.register("get_env_var", self._get_env_var)

    def register(self, name, func, aliases=()):
        """注册Mock工具（可带隐藏别名）"""
        self.tools[name] = func
        for alias in aliases:
            self.aliases[alias] = name

    def unregister(self, name):
        canonical = self.aliases.get(name, name)
        if canonical in self.tools:
            del self.tools[canonical]
        for alias in [k for k, v in self.aliases.items() if v == canonical]:
            del self.aliases[alias]

    def list_tools(self):
        """列出所有Mock工具（只显示规范名）"""
        return list(self.tools.keys())

    def call(self, name: str, params: dict):
        """调用Mock工具"""
        canonical = self.aliases.get(name, name)
        if canonical not in self.tools:
            return {"success": False, "error": f"工具未注册: {name}",
                    "error_code": "TOOL_NOT_FOUND", "tool": name}

        try:
            result = self.tools[canonical](**(params or {}))
            return {
                "success": True,
                "tool": canonical,
                "result": result
            }
        except Exception as e:
            return {
                "success": False,
                "tool": canonical,
                "error": str(e),
                "error_code": "TOOL_ERROR",
            }

    # =====================================================
    # Mock 文件工具
    # =====================================================

    def _copy_file(self, src, dest, overwrite=False):
        return f"[MOCK] 复制文件: {src} -> {dest}"

    def _move_path(self, src, dest, overwrite=False):
        return f"[MOCK] 移动文件: {src} -> {dest}"

    def _delete_file(self, path):
        return f"[MOCK] 删除文件: {path}"

    def _rename_path(self, src, dest, overwrite=False):
        return f"[MOCK] 重命名文件: {src} -> {dest}"

    # =====================================================
    # Mock 文件夹工具
    # =====================================================

    def _create_directory(self, path, exists="keep"):
        return f"[MOCK] 创建目录: {path}"

    def _delete_directory(self, path):
        return f"[MOCK] 删除目录: {path}"

    def _list_directory(self, path):
        return [{"name": f"mock_file_1.txt", "type": "file"},
                {"name": f"mock_folder", "type": "directory"}]

    def _sort_directory(self, path, sort_by="name", reverse=False):
        """
        Mock 排序目录：返回带元信息的排序模拟结果
        （与正式版 sort_directory 相同的四字段结构）
        """
        return [
            {"name": "mock_folder", "type": "directory", "size": None, "mtime": 1000.0},
            {"name": "mock_a.txt", "type": "file", "size": 10, "mtime": 900.0},
            {"name": "mock_b.txt", "type": "file", "size": 20, "mtime": 950.0},
            {"name": "mock_c.txt", "type": "file", "size": 30, "mtime": 800.0},
        ]

    def _index_files(self, path, recursive=True, persist=None, load=None):
        """Mock 文件索引：返回带 [MOCK] 标记的模拟索引清单"""
        return [
            {"path": f"{path}/mock_a.txt", "name": "mock_a.txt",
             "type": "file", "size": 10, "mtime": 900.0},
            {"path": f"{path}/mock_b.txt", "name": "mock_b.txt",
             "type": "file", "size": 20, "mtime": 950.0},
        ]

    def _manage_archive(self, src_dir=None, dest_zip=None,
                        action="compress", extract_to=None):
        """Mock 压缩/解压：返回带 [MOCK] 标记的模拟结果"""
        if action == "extract":
            if extract_to:
                return f"[MOCK] 已解压: {dest_zip} -> {extract_to}"
            return f"[MOCK] 已解压: {dest_zip} -> {dest_zip}_extracted"
        if dest_zip:
            dest = dest_zip if str(dest_zip).endswith(".zip") else f"{dest_zip}.zip"
            return f"[MOCK] 已打包: {src_dir} -> {dest}"
        return f"[MOCK] 已打包: {src_dir} -> {src_dir}_archive.zip"

    # =====================================================
    # Mock 文件查询
    # =====================================================

    def _file_exists(self, path):
        return True

    def _get_file_size(self, path):
        return 1024

    # =====================================================
    # Mock 文本文件
    # =====================================================

    def _read_text_file(self, path, max_bytes=None):
        return f"[MOCK] 这是文件 {path} 的模拟内容"

    def _write_text_file(self, path, content):
        return f"[MOCK] 已写入: {path}（共 {len(content)} 字符）"

    # =====================================================
    # Mock 系统工具
    # =====================================================

    def _current_directory(self):
        return "[MOCK] /home/user/project"

    def _run_command(self, cmd):
        return {
            "stdout": f"[MOCK] 命令 '{cmd}' 的模拟输出",
            "stderr": "",
            "returncode": 0
        }

    # =====================================================
    # Mock 文件创建
    # =====================================================

    def _create_file(self, path, exists="keep"):
        return f"[MOCK] 已创建文件: {path}"

    # =====================================================
    # Mock 元数据
    # =====================================================

    def _get_path_metadata(self, path):
        return {
            "name": "mock.txt", "path": path, "size": 1024,
            "extension": ".txt", "mime_type": "text/plain",
            "created_time": 1000.0, "modified_time": 1100.0,
            "accessed_time": 1100.0, "is_dir": False,
            "is_hidden": False, "is_readonly": False,
        }

    def _get_directory_metadata(self, path):
        return {
            "file_count": 5, "dir_count": 2, "total_size": 4096,
            "largest_file": path + "/big.bin", "newest_file": path + "/new.txt",
            "oldest_file": path + "/old.txt",
            "ext_stats": {".txt": 3, ".bin": 2},
        }

    def _check_file_access(self, path):
        return {"readable": True, "writable": True, "executable": False}

    # =====================================================
    # Mock 完整性 / 比较
    # =====================================================

    def _file_checksum(self, path, algorithm="sha256"):
        return {"path": path, "algorithm": algorithm, "checksum": "mock_checksum_abcdef"}

    def _compare_files(self, path1, path2):
        return {"same": False, "diff": ["- line1", "+ line1 changed"]}

    # =====================================================
    # Mock 批量
    # =====================================================

    def _batch_copy(self, files, dest_dir):
        return [f"{dest_dir}/{Path(f).name}" for f in files]

    def _batch_move(self, files, dest_dir):
        return [f"{dest_dir}/{Path(f).name}" for f in files]

    # =====================================================
    # Mock 磁盘 / 查找
    # =====================================================

    def _disk_usage(self, path=None):
        return {"path": path or ".", "total": 1000, "used": 600, "free": 400, "percent_used": 60.0}

    def _directory_size(self, path):
        return {"path": path, "size": 2048, "file_count": 4}

    def _find_empty_files(self, directory, recursive=True):
        return [f"{directory}/empty1.txt", f"{directory}/empty2.log"]

    def _find_empty_directories(self, directory, recursive=True):
        return [f"{directory}/empty_dir"]

    def _find_old_files(self, directory, days=365):
        return [f"{directory}/old1.txt"]

    def _find_large_files(self, directory, min_size_mb=100, limit=20):
        return [
            {"path": f"{directory}/large_video.mp4", "size_mb": 1500, "mtime": 1700000000},
            {"path": f"{directory}/backup.zip", "size_mb": 800, "mtime": 1700000000},
        ]

    def _search_files(self, pattern, path="~", max_results=50):
        return [
            {"path": f"{path}/found_file.pdf", "name": "found_file.pdf", "type": "file", "size_kb": 120},
            {"path": f"{path}/found_folder", "name": "found_folder", "type": "directory", "size_kb": 0},
        ]

    def _search_in_files(self, root, keyword, include_exts=None, max_results=20, timeout=10):
        return {
            "count": 1,
            "results": [{
                "path": f"{root}/mock_target.py",
                "line_no": 1,
                "line": f"# 包含关键词 {keyword} 的 mock 行",
                "match": keyword,
            }],
        }

    def _edit_file(self, path, old_text, new_text, occurrence=1):
        return {
            "path": path,
            "replaced": 1,
            "summary": f"[MOCK] 局部编辑：{old_text[:20]} → {new_text[:20]}",
        }

    # =====================================================
    # Mock 图片
    # =====================================================

    def _get_image_metadata(self, path):
        return {"width": 800, "height": 600, "format": "PNG", "mode": "RGB", "exif": {}}

    def _rotate_image(self, path, degrees, output=None):
        out = output or path
        return f"[MOCK] 已旋转 {degrees}°: {path} -> {out}"

    # =====================================================
    # Mock 应用操作
    # =====================================================

    def _list_windows(self):
        return [
            {"hwnd": 101, "title": "QQ", "pid": 1001},
            {"hwnd": 202, "title": "Google Chrome", "pid": 1002},
        ]

    def _find_window(self, title=None, process=None):
        return {"hwnd": 101, "title": title or process or "QQ", "pid": 1001}

    def _activate_window(self, hwnd):
        return f"[MOCK] 窗口已激活: {hwnd}"

    def _send_hotkey(self, keys):
        if isinstance(keys, str):
            keys = [keys]
        return "[MOCK] 已发送快捷键: " + "+".join(str(k) for k in keys)

    def _send_text(self, text):
        return f"[MOCK] 已输入文本: {text}"

    def _take_screenshot(self, output=None):
        return output or "[MOCK] /tmp/mock_screenshot.png"

    def _click_at(self, x, y):
        return f"[MOCK] 已点击 ({x}, {y})"

    # =====================================================
    # Mock 浏览器结构化操作
    # =====================================================

    def _browser_launch(self, port=9222, headless=True, chrome_path=None):
        return "[MOCK] ws://127.0.0.1:9222/devtools/page/mock"

    def _browser_close(self):
        return "浏览器连接已关闭"

    def _browser_navigate(self, url):
        return {"url": url, "title": "[MOCK] " + url}

    def _browser_snapshot(self):
        return [
            {"index": 0, "tag": "input", "role": "input",
             "type": "text", "text": ""},
            {"index": 1, "tag": "button", "role": "button",
             "type": "", "text": "搜索"},
            {"index": 2, "tag": "a", "role": "link",
             "type": "", "text": "结果链接"},
        ]

    def _browser_click(self, index=None, selector=None):
        return {"clicked": True, "index": index, "selector": selector}

    def _browser_type(self, text, index=None, selector=None):
        return {"typed": text, "index": index, "selector": selector}

    def _browser_read_text(self, selector=None):
        return "[MOCK] 页面正文文本内容"

    def _browser_screenshot(self, output=None):
        return output or "[MOCK] /tmp/mock_browser.png"

    def _browser_wait(self, selector=None, text=None, timeout=10):
        if selector:
            return {"matched": "selector", "selector": selector}
        return {"matched": "text", "text": text}

    # =====================================================
    # Mock 系统管理：进程
    # =====================================================

    def _list_processes(self, limit=50, sort_by="cpu"):
        return [
            {"pid": 1, "name": "System", "status": "running",
             "cpu_percent": 0.1, "memory_mb": 10.0, "username": "SYSTEM"},
            {"pid": 1001, "name": "python.exe", "status": "running",
             "cpu_percent": 5.2, "memory_mb": 45.6, "username": "user"},
            {"pid": 1002, "name": "chrome.exe", "status": "running",
             "cpu_percent": 12.3, "memory_mb": 320.0, "username": "user"},
            {"pid": 1003, "name": "explorer.exe", "status": "running",
             "cpu_percent": 0.5, "memory_mb": 80.0, "username": "user"},
        ][:limit]

    def _get_process_info(self, pid):
        return {
            "pid": pid, "name": "python.exe", "status": "running",
            "cpu_percent": 5.2, "memory_mb": 45.6, "username": "user",
            "create_time": 1000000.0, "cwd": "C:\\project",
            "cmdline": "python main.py", "num_threads": 4,
            "connections": [{"fd": 3, "status": "ESTABLISHED", "remote": "127.0.0.1:8080"}],
            "children": [],
        }

    def _kill_process(self, pid):
        return f"[MOCK] 已终止进程: {pid}"

    def _terminate_process(self, pid, timeout=5):
        return f"[MOCK] 已终止进程: {pid}"

    # =====================================================
    # Mock 系统管理：网络
    # =====================================================

    def _network_status(self):
        return {
            "interfaces": [
                {"name": "eth0", "is_up": True, "speed": 1000,
                 "ip": "192.168.1.100", "mac": "00:11:22:33:44:55",
                 "bytes_sent": 1000000, "bytes_recv": 5000000},
                {"name": "lo", "is_up": True, "speed": 0,
                 "ip": "127.0.0.1", "mac": "",
                 "bytes_sent": 50000, "bytes_recv": 50000},
            ],
            "connections": [
                {"type": "SOCK_STREAM", "status": "ESTABLISHED",
                 "local": "192.168.1.100:54321", "remote": "93.184.216.34:80"},
            ],
        }

    def _ping_host(self, host, count=3):
        return {
            "host": host, "reachable": True,
            "avg_ms": 15.3, "packet_loss": 0.0,
            "raw_output": f"[MOCK] Ping {host} 成功",
        }

    # =====================================================
    # Mock 系统管理：环境变量
    # =====================================================

    def _list_env_vars(self, pattern=None):
        all_vars = [
            {"key": "PATH", "value": "C:\\Windows;C:\\Python"},
            {"key": "USERNAME", "value": "user"},
            {"key": "COMPUTERNAME", "value": "DESKTOP-TEST"},
            {"key": "OS", "value": "Windows_NT"},
        ]
        if pattern:
            return [v for v in all_vars if pattern.lower() in v["key"].lower()]
        return all_vars

    def _get_env_var(self, name):
        return {"name": name, "value": f"[MOCK] {name}_value", "exists": True}
