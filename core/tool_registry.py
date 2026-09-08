"""
tool_registry.py
------------------------------------
第4组：Tool Registry（正式版）
统一管理所有OS工具
"""

import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import zipfile
from collections import defaultdict
from functools import cmp_to_key
from pathlib import Path

import psutil

from core.app_controller import get_controller
from core import safety
from core.tool_schema import (
    ToolSpec, ParameterDef, generate_spec_from_function, merge_spec_with_safety,
)


class ToolRegistry:
    """工具注册表"""

    def __init__(self):
        self.tools = {}
        self.aliases = {}          # 旧名(别名) -> 规范名
        self.logs = []
        self._confirm_handler = None
        self._tool_specs = {}      # 规范名 -> ToolSpec
        self._tool_signatures = {} # 规范名 -> sha256 签名
        self._register_defaults()

    @property
    def app(self):
        """惰性创建应用控制器（应用操作工具首次调用时才实例化）"""
        if not hasattr(self, "_app"):
            self._app = get_controller()
        return self._app

    @property
    def browser(self):
        """惰性创建浏览器结构化控制器（浏览器工具首次调用时才实例化）"""
        if not hasattr(self, "_browser"):
            from core.browser_controller import BrowserController
            self._browser = BrowserController()
        return self._browser

    @staticmethod
    def _expand_user_paths(params: dict) -> dict:
        """展开路径参数中的 ~ 为用户目录"""
        expanded = {}
        for key, value in params.items():
            if isinstance(value, str):
                expanded[key] = os.path.expanduser(value)
            elif isinstance(value, dict):
                expanded[key] = ToolRegistry._expand_user_paths(value)
            elif isinstance(value, list):
                expanded[key] = [
                    os.path.expanduser(item) if isinstance(item, str) and "~" in item
                    else item
                    for item in value
                ]
            else:
                expanded[key] = value
        return expanded

    # =====================================================
    # 注册默认工具
    # =====================================================

    def _register_defaults(self):

        # 文件操作
        self.register("copy_file", self.copy_file)
        self.register("move_path", self.move_path, aliases=("move_file",))
        self.register("delete_file", self.delete_file)
        self.register("rename_path", self.rename_path, aliases=("rename_file",))
        self.register("create_file", self.create_file)

        # 文件夹操作
        self.register("create_directory", self.create_directory, aliases=("create_folder",))
        self.register("delete_directory", self.delete_directory, aliases=("delete_folder",))
        self.register("list_directory", self.list_directory)
        self.register("sort_directory", self.sort_directory)
        self.register("index_files", self.index_files)
        self.register("manage_archive", self.manage_archive)

        # 文件信息
        self.register("file_exists", self.file_exists)
        self.register("get_file_size", self.get_file_size)
        self.register("get_path_metadata", self.get_path_metadata, aliases=("get_file_metadata",))
        self.register("get_directory_metadata", self.get_directory_metadata, aliases=("get_folder_metadata",))
        self.register("check_file_access", self.check_file_access)

        # 完整性 / 比较
        self.register("file_checksum", self.file_checksum)
        self.register("compare_files", self.compare_files)

        # 文本文件
        self.register("read_text_file", self.read_text_file)
        self.register("write_text_file", self.write_text_file)

        # 批量操作
        self.register("batch_copy", self.batch_copy)
        self.register("batch_move", self.batch_move)

        # 磁盘 / 查找
        self.register("disk_usage", self.disk_usage)
        self.register("directory_size", self.directory_size)
        self.register("find_empty_files", self.find_empty_files)
        self.register("find_empty_directories", self.find_empty_directories)
        self.register("find_old_files", self.find_old_files)
        self.register("find_large_files", self.find_large_files)
        self.register("search_files", self.search_files)
        self.register("search_in_files", self.search_in_files)
        self.register("edit_file", self.edit_file)

        # 图片（需 Pillow）
        self.register("get_image_metadata", self.get_image_metadata)
        self.register("rotate_image", self.rotate_image)

        # 系统工具
        self.register("current_directory", self.current_directory)
        self.register("run_command", self.run_command)

        # 应用操作（窗口管理 / 快捷键 / 文本输入 / 截图 / 鼠标点击）
        self.register("list_windows", self.list_windows)
        self.register("find_window", self.find_window)
        self.register("activate_window", self.activate_window)
        self.register("send_hotkey", self.send_hotkey)
        self.register("send_text", self.send_text)
        self.register("take_screenshot", self.take_screenshot)
        self.register("click_at", self.click_at)

        # 浏览器结构化操作（基于 CDP）
        self.register("browser_launch", self.browser_launch)
        self.register("browser_close", self.browser_close)
        self.register("browser_navigate", self.browser_navigate)
        self.register("browser_snapshot", self.browser_snapshot)
        self.register("browser_click", self.browser_click)
        self.register("browser_type", self.browser_type)
        self.register("browser_read_text", self.browser_read_text)
        self.register("browser_screenshot", self.browser_screenshot)
        self.register("browser_wait", self.browser_wait)

        # 系统管理（进程 / 网络 / 环境变量）
        self.register("list_processes", self.list_processes)
        self.register("get_process_info", self.get_process_info)
        self.register("kill_process", self.kill_process)
        self.register("terminate_process", self.terminate_process)
        self.register("network_status", self.network_status)
        self.register("ping_host", self.ping_host)
        self.register("list_env_vars", self.list_env_vars)
        self.register("get_env_var", self.get_env_var)

    # =====================================================
    # Tool Registry
    # =====================================================

    def register(self, name: str, func, aliases=()):
        """注册工具（可带隐藏别名，别名不显示在工具列表中，但仍可调用）"""
        self.tools[name] = func
        for alias in aliases:
            self.aliases[alias] = name
        # 自动生成工具描述 spec（惰性，首次访问时构建）
        self._tool_specs.pop(name, None)
        # 自动生成工具签名
        self._tool_signatures[name] = self._compute_signature(func)

    def register_tool(self, name: str, func, description: str = "",
                      parameters: list = None, category: str = "通用",
                      risk: str = "low", aliases: tuple = ()):
        """
        注册工具并附带完整描述信息（推荐方式）。

        参数：
            name:        工具名称
            func:        工具函数
            description: 工具描述（供 LLM 理解工具用途）
            parameters:  ParameterDef 列表，描述每个参数
            category:    工具类别（如"文件操作"、"系统管理"）
            risk:        风险等级（read_only/low/medium/high）
            aliases:     隐藏别名

        如果未提供 description/parameters，会自动从函数签名和文档推断。
        """
        self.tools[name] = func
        for alias in aliases:
            self.aliases[alias] = name

        # 构建或自动生成 spec
        if not description and not parameters:
            # 没有任何额外信息，自动生成
            spec = generate_spec_from_function(name, func)
            meta = safety.get_meta("tool", name)
            spec = merge_spec_with_safety(spec, meta)
        else:
            # 使用提供的参数
            spec = ToolSpec(
                name=name,
                description=description or name,
                parameters=parameters or [],
                category=category,
                risk=risk,
            )

        self._tool_specs[name] = spec
        self._tool_signatures[name] = self._compute_signature(func)

    def unregister(self, name: str):
        """注销工具（含其别名）"""

        canonical = self.aliases.get(name, name)
        if canonical in self.tools:
            del self.tools[canonical]
        # 移除指向该规范名的所有别名
        for alias in [k for k, v in self.aliases.items() if v == canonical]:
            del self.aliases[alias]

    def list_tools(self):
        """查看所有工具（只显示规范名，不含隐藏别名）"""

        return list(self.tools.keys())

    def resolve_tool(self, name: str):
        """把别名/规范名解析为规范名；未知名字返回 None"""
        if name in self.tools:
            return name
        return self.aliases.get(name)

    # =====================================================
    # 工具描述 Schema (ToolSpec)
    # =====================================================

    def _ensure_spec(self, name: str):
        """确保 name 对应的工具已生成 ToolSpec，返回 None 表示工具不存在"""
        canonical = self.resolve_tool(name) or name
        if canonical not in self.tools:
            return None
        if canonical not in self._tool_specs:
            func = self.tools[canonical]
            spec = generate_spec_from_function(canonical, func)
            meta = safety.get_meta("tool", canonical)
            spec = merge_spec_with_safety(spec, meta)
            self._tool_specs[canonical] = spec
        return self._tool_specs[canonical]

    def get_tool_spec(self, name: str) -> dict:
        """
        获取指定工具的完整描述规范。

        返回 dict 格式的 ToolSpec，包含 name/description/parameters/category/risk。
        """
        spec = self._ensure_spec(name)
        if spec is None:
            return {"error": f"工具未注册: {name}", "error_code": "TOOL_NOT_FOUND"}
        return spec.to_dict()

    def list_tools_specs(self) -> list:
        """
        列出所有工具的描述规范。

        返回 list[dict]，每个 dict 包含 name/description/parameters/category/risk。
        """
        return [
            self._ensure_spec(name).to_dict()
            for name in self.list_tools()
            if self._ensure_spec(name) is not None
        ]

    # =====================================================
    # MCP 协议兼容接口
    # =====================================================

    def list_tools_mcp(self) -> list:
        """
        按 MCP tools/list 格式返回工具列表。

        每个工具包含 name/description/inputSchema(JSON Schema)。
        上层 Agent 可直接用此格式驱动 LLM 的 tool calling。
        """
        return [
            self._ensure_spec(name).to_mcp_format()
            for name in self.list_tools()
            if self._ensure_spec(name) is not None
        ]

    def list_tools_openai(self) -> list:
        """
        按 OpenAI Function Calling 格式返回工具列表。

        每个工具包含 type/function.name/function.description/function.parameters。
        """
        return [
            self._ensure_spec(name).to_openai_format()
            for name in self.list_tools()
            if self._ensure_spec(name) is not None
        ]

    def list_tools_anthropic(self) -> list:
        """
        按 Anthropic Tool Use 格式返回工具列表。

        每个工具包含 name/description/input_schema。
        """
        return [
            self._ensure_spec(name).to_anthropic_format()
            for name in self.list_tools()
            if self._ensure_spec(name) is not None
        ]

    # =====================================================
    # 工具签名（Source Verification）
    # =====================================================

    @staticmethod
    def _compute_signature(func) -> str:
        """计算函数的 SHA256 签名"""
        import hashlib
        code = getattr(func, "__code__", None)
        if code is None:
            return "unknown"
        return hashlib.sha256(code.co_code).hexdigest()[:16]

    def get_tool_signature(self, name: str) -> dict:
        """获取工具的签名信息"""
        canonical = self.resolve_tool(name) or name
        if canonical not in self.tools:
            return {"error": f"工具未注册: {name}", "error_code": "TOOL_NOT_FOUND"}
        sig = self._tool_signatures.get(canonical, "unknown")
        return {
            "tool": canonical,
            "signature": sig,
            "verified": True,
        }

    def list_tool_signatures(self) -> list:
        """列出所有工具的签名"""
        return [
            {"tool": name, "signature": self._tool_signatures.get(name, "unknown")}
            for name in self.list_tools()
        ]

    def verify_tool_integrity(self, name: str) -> dict:
        """
        验证工具的完整性（签名是否匹配）。

        重新计算当前函数的签名，与注册时存储的签名比对。
        不匹配说明工具函数可能被篡改。
        """
        canonical = self.resolve_tool(name) or name
        if canonical not in self.tools:
            return {"error": f"工具未注册: {name}", "error_code": "TOOL_NOT_FOUND"}
        current = self._compute_signature(self.tools[canonical])
        stored = self._tool_signatures.get(canonical, "")
        match = current == stored
        return {
            "tool": canonical,
            "signature_match": match,
            "current_signature": current,
            "stored_signature": stored,
            "verified": match,
        }

    # =====================================================
    # 安全分级 + 确认门
    # =====================================================

    def set_confirm_handler(self, fn):
        """注入确认处理器 fn(preview) -> bool；拒绝时抛 safety.ConfirmationDenied"""
        self._confirm_handler = fn

    def get_tool_meta(self, name):
        """获取工具的元数据（风险/类别/描述）"""
        canonical = self.resolve_tool(name) or name
        return safety.get_meta("tool", canonical)

    def list_tools_meta(self):
        """列出所有工具及其元数据"""
        return {name: self.get_tool_meta(name) for name in self.list_tools()}

    def call_safely(self, name, params=None, confirm_handler=None):
        """
        带确认门的工具调用：high 风险工具执行前先征求许可。

        与 call() 的区别：call() 直接执行（供纯逻辑/测试/只读场景用），
        call_safely() 在不可逆动作前插入确认门。
        """
        params = params or {}
        canonical = self.resolve_tool(name) or name
        meta = self.get_tool_meta(canonical)

        if safety.requires_confirmation(meta["risk"]):
            handler = confirm_handler or self._confirm_handler
            if handler is None:
                return {
                    "success": False,
                    "tool": name,
                    "error": "该操作不可逆，需要先配置确认处理器",
                    "error_code": "CONFIRMATION_REQUIRED",
                }
            pv = safety.preview("tool", canonical, params, meta)
            try:
                if not handler(pv):
                    return {
                        "success": False,
                        "tool": name,
                        "error": "用户拒绝执行",
                        "error_code": "CONFIRMATION_DENIED",
                    }
            except safety.ConfirmationDenied:
                return {
                    "success": False,
                    "tool": name,
                    "error": "用户拒绝执行",
                    "error_code": "CONFIRMATION_DENIED",
                }

        return self.call(name, params)

    def get_logs(self):
        """获取调用日志"""
        return self.logs

    def get_stats(self):
        """
        获取工具调用统计信息

        返回：
            total_calls: 总调用次数
            by_tool: 各工具调用次数
            success_rate: 成功率（百分比）
            most_used: 最常用工具Top5
        """
        total = len(self.logs)
        if total == 0:
            return {
                "total_calls": 0,
                "by_tool": {},
                "success_rate": 0.0,
                "most_used": []
            }

        # 统计各工具调用次数
        by_tool = {}
        success_count = 0
        for log in self.logs:
            name = log["tool"]
            by_tool[name] = by_tool.get(name, 0) + 1
            if log.get("success"):
                success_count += 1

        # 按调用次数排序
        sorted_tools = sorted(
            by_tool.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return {
            "total_calls": total,
            "by_tool": by_tool,
            "success_rate": round(success_count / total * 100, 1),
            "most_used": [
                {"tool": name, "calls": count}
                for name, count in sorted_tools[:5]
            ]
        }

    def clear_logs(self):
        """清空调用日志"""
        self.logs.clear()
        return "日志已清空"

    def call(self, name: str, params: dict = None):

        params = params or {}

        # 展开 ~ 为用户目录（所有字符串参数）
        params = self._expand_user_paths(params)

        canonical = self.resolve_tool(name)
        if canonical is None:
            return {
                "success": False,
                "tool": name,
                "error": "工具未注册",
                "error_code": "TOOL_NOT_FOUND",
                "suggestion": f"该工具未注册；可用 list_tools() 查看规范名（含可用别名）",
            }

        try:

            result = self.tools[canonical](**params)

            self.logs.append({
                "tool": canonical,
                "params": params,
                "success": True
            })

            return {
                "success": True,
                "tool": canonical,
                "result": result
            }

        except Exception as e:

            self.logs.append({
                "tool": canonical,
                "params": params,
                "success": False,
                "error": str(e)
            })

            return {
                "success": False,
                "tool": canonical,
                "error": str(e),
                "error_code": "TOOL_ERROR",
            }

    # =====================================================
    # 文件工具
    # =====================================================

    def copy_file(self, src: str, dest: str, overwrite: bool = False):

        src = Path(src)
        dest = Path(dest)

        if not src.exists():
            raise FileNotFoundError(src)
        if dest.exists() and not overwrite:
            raise FileExistsError(dest)

        shutil.copy2(str(src), str(dest))

        return f"已复制：{src} -> {dest}"

    def move_path(self, src: str, dest: str, overwrite: bool = False):

        src = Path(src)
        dest = Path(dest)

        if not src.exists():
            raise FileNotFoundError(src)
        if dest.exists() and not overwrite:
            raise FileExistsError(dest)

        shutil.move(str(src), str(dest))

        return f"已移动：{src} -> {dest}"

    def delete_file(self, path: str):

        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(path)

        path.unlink()

        return f"已删除：{path}"

    def rename_path(self, src: str, dest: str, overwrite: bool = False):

        src = Path(src)
        dest = Path(dest)

        if not src.exists():
            raise FileNotFoundError(src)
        if dest.exists() and not overwrite:
            raise FileExistsError(dest)

        src.rename(dest)

        return f"已重命名：{src} -> {dest}"

    # =====================================================
    # 文件夹工具
    # =====================================================

    def create_directory(self, path: str, exists: str = "keep"):

        p = Path(path)

        if p.exists():
            if exists == "error":
                raise FileExistsError(p)
            if not p.is_dir():
                raise NotADirectoryError(p)
        else:
            p.mkdir(parents=True, exist_ok=True)

        return f"目录创建成功：{path}"

    def delete_directory(self, path: str):

        shutil.rmtree(path)

        return f"目录删除成功：{path}"

    def list_directory(self, path: str):

        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(path)

        result = []

        for item in path.iterdir():

            result.append({

                "name": item.name,

                "type": "directory" if item.is_dir() else "file"

            })

        return result

    def sort_directory(self, path: str, sort_by: str = "name", reverse: bool = False):
        """
        排序目录内容（带元信息）

        参数：
            path:    目标目录
            sort_by: 排序键，可选 name / size / mtime，默认 name
            reverse: 是否降序，默认 False（升序）

        返回：
            排序后的条目列表，每条目恒定包含：
                {name, type, size, mtime}
            - type:  "directory" / "file"
            - size:  目录为 None（目录无意义大小），文件为字节数
            - mtime: 最后修改时间（时间戳）

        排序规则：
            - 目录始终优先于文件（ls 风格）
            - 排序键相等时按 name 次级升序兜底，保证结果确定
            - reverse 仅作用于主排序键，次级 name 恒升序
        """
        items = self._sorted_items(Path(path), sort_by, reverse)
        return items

    def _sorted_items(self, path: Path, sort_by: str, reverse: bool) -> list:
        """
        私有辅助：读取目录、附加元信息并按给定键排序
        """
        if not path.exists():
            raise FileNotFoundError(path)
        if not path.is_dir():
            raise NotADirectoryError(path)

        if sort_by not in ("name", "size", "mtime", "type"):
            raise ValueError(f"非法排序键: {sort_by}（可选 name / size / mtime / type）")

        entries = []
        for item in path.iterdir():
            if item.is_dir():
                size = None
            else:
                size = item.stat().st_size
            entries.append({
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": size,
                "mtime": item.stat().st_mtime,
            })

        # 排序规则：
        #   1) 目录始终优先于文件（不受 reverse 影响）
        #   2) 主排序键按 reverse 决定升降序
        #   3) 主键相等时按 name 恒升序兜底，保证确定性
        # 用 cmp_to_key 精确控制：reverse 只作用于主键，
        # 目录优先标志与次级 name 不被 reverse 翻转。

        def _cmp(a, b):
            dir_cmp = (a["type"] != "directory") - (b["type"] != "directory")
            if dir_cmp:
                return dir_cmp
            a_val, b_val = a[sort_by], b[sort_by]
            if sort_by != "name":
                # None 视为"小于任何数值"；两者皆为 None 时直接落入 name 兜底
                if a_val is None or b_val is None:
                    if a_val is None and b_val is None:
                        return (a["name"] > b["name"]) - (a["name"] < b["name"])
                    if a_val is None:
                        return -1 if reverse else 1
                    return 1 if reverse else -1
                order = (a_val > b_val) - (a_val < b_val)
                if reverse:
                    order = -order
                if order:
                    return order
                # 主键相等 -> name 恒升序兜底
                return (a["name"] > b["name"]) - (a["name"] < b["name"])
            else:
                # name 本身就是主键：直接按 reverse 比较 name
                order = (a_val > b_val) - (a_val < b_val)
                return -order if reverse else order

        entries.sort(key=cmp_to_key(_cmp))
        return entries

    # =====================================================
    # 索引工具
    # =====================================================

    def index_files(self, path: str, recursive: bool = True,
                    persist: str = None, load: str = None):
        """
        文件元信息索引（支持持久化/加载）

        参数：
            path:      目标目录（recursive 模式）或非必需（load 模式可传任意占位）
            recursive: 是否递归收集子目录，默认 True
            persist:   非空时把索引结果保存为 JSON 文件（覆盖写入）
            load:      非空时改为读取该 JSON 文件并返回索引（不遍历文件系统）

        返回：
            索引条目列表，每条目恒定包含：
                {path, name, type, size, mtime}
            - path:  绝对路径
            - type:  "directory" / "file"
            - size:  目录为 None，文件为字节数
            - mtime: 最后修改时间（时间戳）

        边界：
            - 空目录返回空列表
            - path 不存在 / 非目录 -> 失败
            - persist 写入失败 -> 失败
            - load 的文件不存在 -> 失败
        """
        if load:
            load_path = Path(load)
            if not load_path.exists() or not load_path.is_file():
                raise FileNotFoundError(load_path)
            with open(load_path, "r", encoding="utf-8") as f:
                return json.load(f)

        root = Path(path)
        if not root.exists():
            raise FileNotFoundError(root)
        if not root.is_dir():
            raise NotADirectoryError(root)

        entries = []
        iterator = root.rglob("*") if recursive else root.iterdir()
        for item in iterator:
            if item.is_dir():
                size = None
                item_type = "directory"
            else:
                size = item.stat().st_size
                item_type = "file"
            entries.append({
                "path": str(item.absolute()),
                "name": item.name,
                "type": item_type,
                "size": size,
                "mtime": item.stat().st_mtime,
            })

        if persist:
            persist_path = Path(persist)
            persist_path.parent.mkdir(parents=True, exist_ok=True)
            with open(persist_path, "w", encoding="utf-8") as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)

        return entries

    # =====================================================
    # 归档工具
    # =====================================================

    def manage_archive(self, src_dir: str = None, dest_zip: str = None,
                       action: str = "compress", extract_to: str = None):
        """
        管理 zip 归档（压缩 / 解压一体）

        参数：
            action:    "compress"（压缩，默认）或 "extract"（解压）
            src_dir:   compress 模式：源目录（须存在且为目录）
            dest_zip:  compress：目标 zip 路径（不带 .zip 自动补，
                       未指定时默认 {源目录名}_archive.zip；已存在覆盖）
                       extract：待解压的 zip 路径（须存在）
            extract_to: extract 模式：解压目标目录；
                        未指定时默认在 zip 同级的 {zip名}_extracted

        返回：
            compress 模式 -> zip 文件绝对路径
            extract  模式 -> 解压后的目录绝对路径

        边界：
            - compress：src_dir 不存在 / 非目录 -> 失败
            - extract ：dest_zip 不存在 / 非 zip -> 失败
            - extract ：解压目标目录已存在 -> 失败（防覆盖误删）
            - 写入失败（目标不可写）-> 失败
        """
        if action not in ("compress", "extract"):
            raise ValueError(f"非法 action: {action}（可选 compress / extract）")

        if action == "compress":
            return self._compress_archive(src_dir, dest_zip)
        return self._extract_archive(dest_zip, extract_to)

    # ---------- 压缩 ----------

    def _compress_archive(self, src_dir: str, dest_zip: str):
        """私有辅助：把目录打包为 zip"""
        src = Path(src_dir)
        if not src.exists():
            raise FileNotFoundError(src)
        if not src.is_dir():
            raise NotADirectoryError(src)

        if dest_zip:
            dest = Path(dest_zip)
        else:
            dest = src.parent / f"{src.name}_archive.zip"

        # make_archive 会自动补 .zip；去掉可能带上的后缀避免双写 .zip
        if dest.suffix == ".zip":
            base_name = str(dest.with_suffix(""))
        else:
            base_name = str(dest)

        # root_dir=src 使 zip 内路径相对源目录根（解压即得该目录内容）
        result = shutil.make_archive(base_name, "zip", root_dir=str(src))
        return str(Path(result).absolute())

    # ---------- 解压 ----------

    def _extract_archive(self, dest_zip: str, extract_to: str):
        """私有辅助：把 zip 解压到目标目录"""
        zip_path = Path(dest_zip)
        if not zip_path.exists() or not zip_path.is_file():
            raise FileNotFoundError(zip_path)

        if extract_to:
            out = Path(extract_to)
        else:
            out = zip_path.parent / (zip_path.stem + "_extracted")

        if out.exists():
            raise FileExistsError(out)

        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(str(out))
        return str(out.absolute())

    # =====================================================
    # 文件查询
    # =====================================================

    def file_exists(self, path: str):

        return Path(path).exists()

    def get_file_size(self, path: str):

        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(path)

        return path.stat().st_size

    # =====================================================
    # 文本文件
    # =====================================================

    def read_text_file(self, path: str, max_bytes: int = None):
        """
        读取文本文件

        参数：
            path:      文件路径
            max_bytes: 读取大小上限（字节），超过则报错；None（默认）不设限

        返回：
            文件文本内容

        说明：
            若检测到文件含二进制内容（NUL 字节），视为非文本文件而报错。
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        if max_bytes is not None and p.stat().st_size > max_bytes:
            raise ValueError(f"文件过大（{p.stat().st_size} 字节），超过上限 {max_bytes}")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def write_text_file(self, path: str, content: str):

        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        return f"写入完成：{path}"

    # =====================================================
    # 系统工具
    # =====================================================

    def current_directory(self):

        return str(Path.cwd())

    def run_command(self, cmd: str):

        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True
        )

        return {

            "stdout": result.stdout,

            "stderr": result.stderr,

            "returncode": result.returncode

        }

    # =====================================================
    # 新增：文件创建
    # =====================================================

    def create_file(self, path: str, exists: str = "keep"):
        """
        创建空文件

        参数：
            path:   要创建的文件路径
            exists: 文件已存在时的行为：
                    - "keep"（默认）: 保留已有内容，幂等返回
                    - "overwrite":   清空后返回
                    - "error":       报错

        返回：
            创建成功的文件绝对路径
        """
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            if exists == "error":
                raise FileExistsError(p)
            if exists == "overwrite":
                with open(p, "w") as _f:
                    pass
        else:
            open(p, "a").close()
        return str(p.absolute())

    # =====================================================
    # 新增：文件 / 文件夹元数据
    # =====================================================

    def get_path_metadata(self, path: str):
        """
        获取文件完整元数据

        参数：
            path: 文件路径

        返回：
            {name, path, size, extension, mime_type, created_time,
             modified_time, accessed_time, is_dir, is_hidden, is_readonly}
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        st = p.stat()
        return {
            "name": p.name,
            "path": str(p.absolute()),
            "size": st.st_size,
            "extension": p.suffix.lower(),
            "mime_type": mimetypes.guess_type(p.name)[0],
            "created_time": st.st_ctime,
            "modified_time": st.st_mtime,
            "accessed_time": st.st_atime,
            "is_dir": p.is_dir(),
            "is_hidden": p.name.startswith("."),
            "is_readonly": not (st.st_mode & 0o222),
        }

    def get_directory_metadata(self, path: str):
        """
        获取文件夹统计信息

        参数：
            path: 目标目录

        返回：
            {file_count, dir_count, total_size, largest_file, newest_file, oldest_file, ext_stats}
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        if not p.is_dir():
            raise NotADirectoryError(p)

        file_count = 0
        dir_count = 0
        total_size = 0
        ext_stats = defaultdict(int)
        largest = None
        newest = None
        oldest = None

        for item in p.rglob("*"):
            if item.is_dir():
                dir_count += 1
                continue
            file_count += 1
            size = item.stat().st_size
            mtime = item.stat().st_mtime
            total_size += size
            ext_stats[item.suffix.lower() or "(无扩展名)"] += 1
            if largest is None or size > largest[1]:
                largest = (str(item.absolute()), size)
            if newest is None or mtime > newest[1]:
                newest = (str(item.absolute()), mtime)
            if oldest is None or mtime < oldest[1]:
                oldest = (str(item.absolute()), mtime)

        return {
            "file_count": file_count,
            "dir_count": dir_count,
            "total_size": total_size,
            "largest_file": largest[0] if largest else None,
            "newest_file": newest[0] if newest else None,
            "oldest_file": oldest[0] if oldest else None,
            "ext_stats": dict(ext_stats),
        }

    def check_file_access(self, path: str):
        """
        检查当前进程对文件的读 / 写 / 执行权限

        参数：
            path: 文件或目录路径

        返回：
            {"readable": bool, "writable": bool, "executable": bool}
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        return {
            "readable": os.access(p, os.R_OK),
            "writable": os.access(p, os.W_OK),
            "executable": os.access(p, os.X_OK),
        }

    # =====================================================
    # 新增：完整性 / 比较
    # =====================================================

    def file_checksum(self, path: str, algorithm: str = "sha256"):
        """
        计算文件哈希（MD5 / SHA1 / SHA256）

        参数：
            path:       文件路径
            algorithm:  哈希算法，可选 sha256（默认） / sha1 / md5

        返回：
            {path, algorithm, checksum}
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        if algorithm not in ("sha256", "sha1", "md5"):
            raise ValueError(f"非法算法: {algorithm}（可选 sha256 / sha1 / md5）")

        h = hashlib.new(algorithm)
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return {
            "path": str(p.absolute()),
            "algorithm": algorithm,
            "checksum": h.hexdigest(),
        }

    def compare_files(self, path1: str, path2: str):
        """
        比较两个文本文件的差异（返回简单标记的 diff 行）

        参数：
            path1: 第一个文件
            path2: 第二个文件

        返回：
            {"same": bool, "diff": [<行>, ...]}
        """
        def _read(p):
            pp = Path(p)
            if not pp.exists():
                raise FileNotFoundError(pp)
            with open(pp, "r", encoding="utf-8", errors="replace") as f:
                return [line.rstrip("\n") for line in f]

        lines1 = _read(path1)
        lines2 = _read(path2)
        if lines1 == lines2:
            return {"same": True, "diff": []}

        out = []
        max_len = max(len(lines1), len(lines2))
        for i in range(max_len):
            l1 = lines1[i] if i < len(lines1) else "<EOF>"
            l2 = lines2[i] if i < len(lines2) else "<EOF>"
            if l1 == l2:
                out.append(f"  {l1}")
            else:
                out.append(f"- {l1}")
                out.append(f"+ {l2}")
        return {"same": False, "diff": out}

    # =====================================================
    # 新增：批量操作
    # =====================================================

    def batch_copy(self, files, dest_dir, overwrite: bool = False):
        """
        一次复制多个文件到目标目录

        参数：
            files:    要复制的文件路径列表
            dest_dir: 目标目录
            overwrite: 目标存在同名文件时是否覆盖，默认 False（报错）

        返回：
            [<已复制文件的绝对路径>, ...]

        说明：
            先做整体预检查（源存在 / 目标冲突），任一不满足则整体报错，
            避免执行了一部分才失败。
        """
        dest = Path(dest_dir)
        targets = []
        for f in files:
            src = Path(f)
            if not src.is_file():
                raise FileNotFoundError(src)
            target = dest / src.name
            if target.exists() and not overwrite:
                raise FileExistsError(target)
            targets.append((src, target))

        dest.mkdir(parents=True, exist_ok=True)
        copied = []
        for src, target in targets:
            shutil.copy2(str(src), str(target))
            copied.append(str(target.absolute()))
        return copied

    def batch_move(self, files, dest_dir, overwrite: bool = False):
        """
        一次移动多个文件到目标目录

        参数：
            files:    要移动的文件路径列表
            dest_dir: 目标目录
            overwrite: 目标存在同名文件时是否覆盖，默认 False（报错）

        返回：
            [<已移动文件的绝对路径>, ...]

        说明：
            先做整体预检查（源存在 / 目标冲突），任一不满足则整体报错，
            避免执行了一部分才失败。
        """
        dest = Path(dest_dir)
        targets = []
        for f in files:
            src = Path(f)
            if not src.is_file():
                raise FileNotFoundError(src)
            target = dest / src.name
            if target.exists() and not overwrite:
                raise FileExistsError(target)
            targets.append((src, target))

        dest.mkdir(parents=True, exist_ok=True)
        moved = []
        for src, target in targets:
            shutil.move(str(src), str(target))
            moved.append(str(target.absolute()))
        return moved

    # =====================================================
    # 新增：磁盘 / 查找
    # =====================================================

    def disk_usage(self, path: str = None):
        """
        查询磁盘空间使用情况

        参数：
            path: 目标路径（默认当前目录所在磁盘）

        返回：
            {"path", "total", "used", "free", "percent_used"}
        """
        target = Path(path) if path else Path.cwd()
        usage = shutil.disk_usage(target)
        percent = round(usage.used / usage.total * 100, 2) if usage.total else 0.0
        return {
            "path": str(target.absolute()),
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent_used": percent,
        }

    def directory_size(self, path: str):
        """
        查询目录占用空间大小

        参数：
            path: 目标目录

        返回：
            {path, size, file_count}
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        total = 0
        count = 0
        for item in p.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
                count += 1
        return {"path": str(p.absolute()), "size": total, "file_count": count}

    def find_empty_files(self, directory, recursive=True):
        """
        查找目录下的空文件（0 字节）

        参数：
            directory: 目标目录
            recursive: 是否递归，默认 True

        返回：
            [<绝对路径>, ...]
        """
        d = Path(directory)
        if not d.exists():
            raise FileNotFoundError(d)
        it = d.rglob("*") if recursive else d.iterdir()
        return [str(p.absolute()) for p in it if p.is_file() and p.stat().st_size == 0]

    def find_empty_directories(self, directory, recursive=True):
        """
        查找目录下的空目录

        参数：
            directory: 目标目录
            recursive: 是否递归，默认 True

        返回：
            [<绝对路径>, ...]
        """
        d = Path(directory)
        if not d.exists():
            raise FileNotFoundError(d)
        it = d.rglob("*") if recursive else d.iterdir()
        return [str(p.absolute()) for p in it if p.is_dir() and not any(p.iterdir())]

    def find_old_files(self, directory, days=365):
        """
        查找目录下长期未修改的文件

        参数：
            directory: 目标目录
            days:      距今多少天未修改视为"旧"，默认 365

        返回：
            [<绝对路径>, ...]
        """
        d = Path(directory)
        if not d.exists():
            raise FileNotFoundError(d)
        cutoff = time.time() - days * 86400
        return [str(p.absolute()) for p in d.rglob("*")
                if p.is_file() and p.stat().st_mtime <= cutoff]

    def find_large_files(self, directory, min_size_mb=100, limit=20, timeout=5):
        """
        查找目录下的大文件

        参数：
            directory:   目标目录
            min_size_mb: 最小文件大小（MB），默认 100
            limit:       返回文件数量上限，默认 20
            timeout:     最大搜索时间（秒），默认 5，超时返回已有结果

        返回：
            [{path, size_mb, mtime}, ...] 按大小降序
        """
        d = Path(directory)
        if not d.exists():
            raise FileNotFoundError(d)

        min_bytes = min_size_mb * 1024 * 1024
        results = []
        start_time = time.time()

        for p in d.rglob("*"):
            if time.time() - start_time > timeout:
                break
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
                if size >= min_bytes:
                    results.append({
                        "path": str(p.absolute()),
                        "size_mb": round(size / (1024 * 1024), 1),
                        "mtime": p.stat().st_mtime,
                    })
            except (OSError, PermissionError):
                continue

        results.sort(key=lambda x: x["size_mb"], reverse=True)
        return results[:limit]

    def search_files(self, pattern, path="~", max_results=50, timeout=5):
        """
        按名称模式搜索文件（glob 风格）

        参数：
            pattern:     文件名模式，如 *.pdf、报告、*.txt
            path:        搜索根目录，默认 ~（用户目录）
            max_results: 结果数量上限，默认 50
            timeout:     最大搜索时间（秒），默认 5，超时返回已有结果

        返回：
            [{path, name, type, size_kb}, ...]
        """
        d = Path(path).expanduser()
        if not d.exists():
            raise FileNotFoundError(d)

        results = []
        start_time = time.time()
        scanned = 0

        # 如果没有通配符，当作普通关键词搜索
        if "*" not in pattern and "?" not in pattern:
            pattern_lower = pattern.lower()
            for p in d.rglob("*"):
                # 超时检查
                if time.time() - start_time > timeout:
                    break
                scanned += 1
                if pattern_lower in p.name.lower():
                    try:
                        stat = p.stat()
                        results.append({
                            "path": str(p.absolute()),
                            "name": p.name,
                            "type": "directory" if p.is_dir() else "file",
                            "size_kb": round(stat.st_size / 1024, 1) if p.is_file() else 0,
                        })
                    except (OSError, PermissionError):
                        continue
                    if len(results) >= max_results:
                        break
        else:
            # glob 通配符搜索
            for p in d.rglob(pattern):
                if time.time() - start_time > timeout:
                    break
                scanned += 1
                try:
                    stat = p.stat()
                    results.append({
                        "path": str(p.absolute()),
                        "name": p.name,
                        "type": "directory" if p.is_dir() else "file",
                        "size_kb": round(stat.st_size / 1024, 1) if p.is_file() else 0,
                    })
                except (OSError, PermissionError):
                    continue
                if len(results) >= max_results:
                    break

        return results

    def search_in_files(self, root, keyword, include_exts=None, max_results=20, timeout=10):
        """
        在目录内的文本文件中搜索关键词/正则（内容搜索，区别于 search_files 的文件名搜索）

        参数：
            root:         搜索根目录（绝对路径）
            keyword:      要搜索的关键词或正则表达式（如 "def search_in_files"、"TODO"、"报错关键字"）
            include_exts: 限定文件扩展名列表（如 [".py", ".js"]）；None（默认）自动跳过
                          二进制与常见无关目录（.git/node_modules/venv/__pycache__ 等）
            max_results:  结果数量上限，默认 20
            timeout:      最大搜索时间（秒），默认 10

        返回：
            {"count": 命中总数, "results": [{"path", "line_no", "line", "match"}, ...]}
        """
        d = Path(root).expanduser()
        if not d.exists():
            raise FileNotFoundError(d)
        if not keyword:
            raise ValueError("keyword 不能为空")

        try:
            pattern = re.compile(keyword, re.IGNORECASE)
        except re.error as e:
            raise ValueError(f"keyword 不是有效的正则表达式：{e}（想搜普通文本时直接输入文字即可）")

        skip_dirs = {".git", ".svn", ".hg", "__pycache__", "node_modules",
                     "venv", ".venv", "dist", "build", ".idea", ".vscode",
                     ".mypy_cache", ".pytest_cache", ".next", "site-packages"}
        exts = None
        if include_exts:
            exts = {e.lower() if e.startswith(".") else "." + e.lower() for e in include_exts}

        results = []
        start = time.time()
        for p in d.rglob("*"):
            if time.time() - start > timeout:
                break
            if p.is_dir():
                continue
            if any(part in skip_dirs for part in p.parts):
                continue
            if exts is not None and p.suffix.lower() not in exts:
                continue
            try:
                if p.stat().st_size > 2 * 1024 * 1024:
                    continue
                with open(p, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except UnicodeDecodeError:
                try:
                    with open(p, "r", encoding="gbk", errors="replace") as f:
                        lines = f.readlines()
                except (OSError, PermissionError):
                    continue
            except (OSError, PermissionError):
                continue
            for i, line in enumerate(lines, 1):
                m = pattern.search(line)
                if m:
                    results.append({
                        "path": str(p.absolute()),
                        "line_no": i,
                        "line": line.rstrip("\r\n"),
                        "match": m.group(0),
                    })
                    if len(results) >= max_results:
                        return {"count": len(results), "results": results}
        return {"count": len(results), "results": results}

    def edit_file(self, path, old_text, new_text, occurrence=1):
        """
        局部编辑文本文件：查找一段原文并精确替换（比整文件重写更安全）

        参数：
            path:       要编辑的文件路径
            old_text:   要查找的原文，必须与文件内容**逐字一致**（含缩进/空格/换行），
                        可先用 read_text_file 读取文件核对原文
            new_text:   替换成的新文本（传空串表示删除该段）
            occurrence: 替换第几处匹配，默认 1（第一处）；"all" 或 -1 表示全部替换

        返回：
            {"path", "replaced": 替换次数, "summary": 编辑位置摘要}
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        if not old_text:
            raise ValueError("old_text 不能为空")

        encoding = None
        content = None
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                with open(p, "r", encoding=enc) as f:
                    content = f.read()
                encoding = enc
                break
            except UnicodeDecodeError:
                continue
        if encoding is None:
            raise ValueError("无法识别的文本编码（非 UTF-8 / GBK），请确认是文本文件")

        count = content.count(old_text)
        if count == 0:
            raise ValueError(
                f"old_text 在文件中未找到。请确保 old_text 与文件内容逐字一致"
                "（含缩进、空格、换行），可先用 read_text_file 读取文件核对原文。")
        first_start = content.find(old_text)

        if occurrence in ("all", -1):
            new_content = content.replace(old_text, new_text)
            replaced = count
        else:
            try:
                occ = int(occurrence)
            except (TypeError, ValueError):
                raise ValueError("occurrence 必须是正整数、-1 或 'all'")
            if occ < 1:
                raise ValueError("occurrence 必须是正整数、-1 或 'all'")
            if occ > count:
                raise ValueError(f"occurrence={occ} 超出匹配次数（文件中共 {count} 处）")
            idx = 0
            for _ in range(occ):
                idx = content.find(old_text, idx) + 1
            start = idx - 1
            new_content = content[:start] + new_text + content[start + len(old_text):]
            replaced = 1

        with open(p, "w", encoding=encoding) as f:
            f.write(new_content)

        line_no = content.count("\n", 0, first_start) + 1
        line_start = content.rfind("\n", 0, first_start) + 1
        line_end = content.find("\n", first_start)
        if line_end == -1:
            line_end = len(content)
        snippet = content[line_start:line_end].strip()[:120]
        return {
            "path": str(p.absolute()),
            "replaced": replaced,
            "summary": f"第 {line_no} 行：{snippet}",
        }

    # =====================================================
    # 新增：图片（需 Pillow）
    # =====================================================

    @staticmethod
    def _pillow():
        """惰性导入 Pillow，未安装时给出明确提示"""
        try:
            from PIL import Image
            return Image
        except ImportError:
            raise RuntimeError("图片功能需要 Pillow，请先 pip install Pillow")

    def get_image_metadata(self, path: str):
        """
        获取图片元数据（宽高 / 格式 / 色彩模式 / EXIF）

        参数：
            path: 图片路径

        返回：
            {width, height, format, mode, exif}
        """
        Image = self._pillow()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        with Image.open(p) as img:
            exif = {}
            raw = getattr(img, "_getexif", None)
            if callable(raw):
                data = raw() or {}
                for tag, value in data.items():
                    exif[str(tag)] = str(value)
            return {
                "width": img.width,
                "height": img.height,
                "format": img.format,
                "mode": img.mode,
                "exif": exif,
            }

    def rotate_image(self, path: str, degrees: float, output: str = None):
        """
        旋转图片并保存

        参数：
            path:      源图片路径
            degrees:   旋转角度（逆时针，90/180/270 最常用）
            output:    输出路径；未指定则覆盖原文件

        返回：
            保存后的图片绝对路径
        """
        Image = self._pillow()
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(src)
        out = Path(output) if output else src
        with Image.open(src) as img:
            rotated = img.rotate(degrees, expand=True)
            rotated.save(out)
        return str(out.absolute())

    # =====================================================
    # 新增：应用操作（窗口管理 / 快捷键 / 文本输入 / 截图）
    # =====================================================

    def list_windows(self):
        """
        列出所有可见窗口

        返回：
            [{"hwnd": int, "title": str, "pid": int}, ...]
        """
        return self.app.list_windows()

    def find_window(self, title=None, process=None):
        """
        按标题或进程名查找窗口

        参数：
            title:   窗口标题子串（忽略大小写）
            process: 进程名（如 "QQ"、"chrome"）

        返回：
            匹配到的窗口 {"hwnd", "title", "pid"}；找不到抛 LookupError
        """
        win = self.app.find_window(title=title, process=process)
        if win is None:
            desc = title or process or "(无筛选条件)"
            raise LookupError(f"未找到匹配窗口: {desc}")
        return win

    def activate_window(self, hwnd):
        """
        把指定窗口置于前台

        参数：
            hwnd: 窗口句柄（find_window / list_windows 返回的整数）

        返回：
            True 成功；内部已做前台锁定容错
        """
        ok = self.app.activate_window(int(hwnd))
        if not ok:
            raise RuntimeError(f"窗口激活失败: {hwnd}")
        return f"窗口已激活: {hwnd}"

    def send_hotkey(self, keys):
        """
        发送快捷键组合

        参数：
            keys: 键名列表或字符串，如 ["ctrl", "f"]、["alt", "tab"]

        返回：
            "已发送快捷键: ctrl+f"
        """
        if isinstance(keys, str):
            keys = [keys]
        self.app.send_hotkey(keys)
        return "已发送快捷键: " + "+".join(str(k) for k in keys)

    def send_text(self, text):
        """
        向当前焦点输入文本（中文安全，走剪贴板粘贴）

        返回：
            "已输入文本: ..."
        """
        self.app.send_text(text)
        return f"已输入文本: {text}"

    def take_screenshot(self, output=None):
        """
        截取当前屏幕

        参数：
            output: 保存路径；未指定时保存到系统临时目录

        返回：
            截图文件的绝对路径
        """
        return self.app.take_screenshot(output=output)

    def click_at(self, x, y):
        """
        鼠标左键单击屏幕坐标 (x, y)

        参数：
            x, y: 屏幕物理像素坐标（原点左上），与 take_screenshot 同一坐标系

        说明：
            桌面 GUI（尤其 QQ 等自绘 UI）的搜索框 / 结果行通常需要先鼠标点击：
            - 点击搜索框使其获得焦点（Ctrl+F 在部分 QQ 版本上并不聚焦输入框）
            - 点击搜索结果行直达会话（键盘回车可能落入"查看资料"而非进入聊天）

        典型编排：take_screenshot → 视觉定位目标坐标 → click_at(x, y)。
        """
        self.app.click_at(x, y)
        return f"已点击 ({x}, {y})"

    # =====================================================
    # 新增：浏览器结构化操作（基于 CDP）
    # =====================================================

    def browser_launch(self, port=9222, headless=True, chrome_path=None):
        """
        启动并连接一个浏览器（Chrome/Edge/Chromium）

        参数：
            port:        调试端口，默认 9222
            headless:    是否无头模式，默认 True
            chrome_path: 浏览器可执行文件路径（未指定自动探测）

        返回：
            页面 target 的 WebSocket 地址
        """
        return self.browser.launch(port=port, headless=headless, chrome_path=chrome_path)

    def browser_close(self):
        """
        关闭浏览器连接

        返回：
            "浏览器连接已关闭"
        """
        return self.browser.close()

    def browser_navigate(self, url):
        """
        导航到指定 URL（Page.navigate）

        返回：
            {"url": ..., "title": ...}
        """
        return self.browser.navigate(url)

    def browser_snapshot(self):
        """
        提取页面可交互元素索引清单（Agent 的“眼睛”）

        返回：
            [{"index", "tag", "role", "type", "text"}, ...]
        """
        return self.browser.snapshot()

    def browser_click(self, index=None, selector=None):
        """
        点击指定元素（按快照索引或 CSS 选择器）

        参数：
            index:    由 browser_snapshot 返回的元素下标
            selector: CSS 选择器（与 index 二选一）

        返回：
            {"clicked": True, "index", "selector"}
        """
        return self.browser.click(index=index, selector=selector)

    def browser_type(self, text, index=None, selector=None):
        """
        向指定元素输入文本（中文安全，走 CDP Input.insertText）

        参数：
            text:     要输入的文本
            index:    browser_snapshot 返回的元素下标
            selector: CSS 选择器（与 index 二选一）

        返回：
            {"typed", "index", "selector"}
        """
        return self.browser.type_text(text, index=index, selector=selector)

    def browser_read_text(self, selector=None):
        """
        读取页面（或指定元素）文本

        参数：
            selector: CSS 选择器；未指定则读取整个页面正文

        返回：
            文本内容
        """
        return self.browser.read_text(selector=selector)

    def browser_screenshot(self, output=None):
        """
        对当前页面截图（Page.captureScreenshot）

        参数：
            output: 保存路径；未指定时存到系统临时目录

        返回：
            截图文件绝对路径
        """
        return self.browser.screenshot(output=output)

    def browser_wait(self, selector=None, text=None, timeout=10):
        """
        等待某元素或文本出现（事件驱动式等待）

        参数：
            selector: CSS 选择器（可选）
            text:     待出现的文字（可选）
            timeout:  超时秒数，默认 10

        返回：
            {"matched": "selector"/"text", ...}
        """
        return self.browser.wait_for(selector=selector, text=text, timeout=timeout)

    # =====================================================
    # 系统管理工具：进程管理
    # =====================================================

    def list_processes(self, limit: int = 50, sort_by: str = "cpu"):
        """
        列出当前运行的进程。

        参数：
            limit:  返回的最大进程数，默认 50
            sort_by: 排序方式，可选 cpu（CPU占用降序）/ memory（内存降序）/ name（名称升序）

        返回：
            [{"pid": int, "name": str, "status": str, "cpu_percent": float,
              "memory_mb": float, "username": str}, ...]
        """
        attrs = ["pid", "name", "status", "cpu_percent", "memory_info", "username"]
        processes = []
        for proc in psutil.process_iter(attrs):
            try:
                info = proc.info
                processes.append({
                    "pid": info["pid"],
                    "name": info["name"] or "",
                    "status": info["status"] or "",
                    "cpu_percent": info["cpu_percent"] or 0.0,
                    "memory_mb": round((info["memory_info"].rss or 0) / 1024 / 1024, 1),
                    "username": info["username"] or "",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # 排序
        reverse = True
        if sort_by == "name":
            reverse = False
            key_fn = lambda p: p["name"].lower()
        elif sort_by == "memory":
            key_fn = lambda p: p["memory_mb"]
        else:  # cpu
            key_fn = lambda p: p["cpu_percent"]

        processes.sort(key=key_fn, reverse=reverse)
        return processes[:limit]

    def get_process_info(self, pid: int):
        """
        获取指定进程的详细信息。

        参数：
            pid: 进程 ID

        返回：
            {pid, name, status, cpu_percent, memory_mb, username,
             create_time, cwd, cmdline, num_threads, connections}
        """
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                mem = p.memory_info()
                cwd = ""
                try:
                    cwd = p.cwd()
                except (psutil.AccessDenied, FileNotFoundError):
                    cwd = "(无权限访问)"
                conns = []
                try:
                    for conn in p.connections()[:20]:
                        raddr = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A"
                        conns.append({"fd": conn.fd, "status": conn.status, "remote": raddr})
                except psutil.AccessDenied:
                    conns = ["(无权限访问)"]
                try:
                    children = [c.pid for c in p.children()]
                except psutil.AccessDenied:
                    children = []

                return {
                    "pid": p.pid,
                    "name": p.name(),
                    "status": p.status(),
                    "cpu_percent": p.cpu_percent(interval=0.1),
                    "memory_mb": round(mem.rss / 1024 / 1024, 1),
                    "username": p.username(),
                    "create_time": p.create_time(),
                    "cwd": cwd,
                    "cmdline": " ".join(p.cmdline()) if p.cmdline() else "",
                    "num_threads": p.num_threads(),
                    "connections": conns,
                    "children": children,
                }
        except psutil.NoSuchProcess:
            raise LookupError(f"进程不存在: pid={pid}")
        except psutil.AccessDenied:
            raise PermissionError(f"无权限访问进程: pid={pid}")

    def kill_process(self, pid: int):
        """
        强制终止指定进程（SIGKILL/TerminateProcess）。

        参数：
            pid: 进程 ID

        返回：
            "已终止进程: {pid}"
        """
        try:
            p = psutil.Process(pid)
            p.kill()
            return f"已终止进程: {pid}"
        except psutil.NoSuchProcess:
            raise LookupError(f"进程不存在: pid={pid}")
        except psutil.AccessDenied:
            raise PermissionError(f"无权限终止进程: pid={pid}")

    def terminate_process(self, pid: int, timeout: int = 5):
        """
        优雅终止指定进程（先 SIGTERM，超时后强制终止）。

        参数：
            pid:     进程 ID
            timeout: 等待优雅退出的秒数，默认 5

        返回：
            "已终止进程: {pid}" 或 "已强制终止进程: {pid}"
        """
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout=timeout)
            return f"已终止进程: {pid}"
        except psutil.TimeoutExpired:
            p.kill()
            return f"已强制终止进程: {pid}（超时）"
        except psutil.NoSuchProcess:
            raise LookupError(f"进程不存在: pid={pid}")
        except psutil.AccessDenied:
            raise PermissionError(f"无权限终止进程: pid={pid}")

    # =====================================================
    # 系统管理工具：网络
    # =====================================================

    def network_status(self):
        """
        获取网络接口状态。

        返回：
            {"interfaces": [{name, is_up, speed, ip, mac, sent, recv}, ...],
             "connections": [{type, status, local, remote}, ...]}
        """
        # 接口状态
        interfaces = []
        if_stats = psutil.net_if_stats()
        if_addrs = psutil.net_if_addrs()
        io_counters = psutil.net_io_counters(pernic=True)

        for name, stats in if_stats.items():
            addrs = if_addrs.get(name, [])
            ipv4 = ""
            mac = ""
            for addr in addrs:
                if addr.family.name == "AF_INET":
                    ipv4 = addr.address
                elif addr.family.name == "AF_PACKET" or (addr.family.name == "AF_LINK" and not mac):
                    mac = addr.address

            io = io_counters.get(name)
            interfaces.append({
                "name": name,
                "is_up": stats.isup,
                "speed": stats.speed,
                "ip": ipv4,
                "mac": mac,
                "bytes_sent": io.bytes_sent if io else 0,
                "bytes_recv": io.bytes_recv if io else 0,
            })

        # 连接摘要
        connections = []
        for conn in psutil.net_connections(kind="inet")[:50]:
            local = f"{conn.laddr.ip}:{conn.laddr.port}" if conn.laddr else "N/A"
            remote = f"{conn.raddr.ip}:{conn.raddr.port}" if conn.raddr else "N/A"
            connections.append({
                "type": conn.type.name,
                "status": conn.status,
                "local": local,
                "remote": remote,
            })

        return {
            "interfaces": interfaces,
            "connections": connections,
        }

    def ping_host(self, host: str, count: int = 3):
        """
        Ping 指定主机，测试网络连通性。

        参数：
            host:  主机名或 IP 地址
            count: 发送的 ping 包数量，默认 3

        返回：
            {"host": str, "reachable": bool, "avg_ms": float, "packet_loss": float}
        """
        import platform as _platform
        param = "-n" if _platform.system().lower() == "windows" else "-c"
        timeout_flag = "-w" if _platform.system().lower() == "windows" else "-W"
        cmd = ["ping", param, str(count), timeout_flag, "3", host]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            success = result.returncode == 0
            # 解析平均延迟
            avg_ms = 0.0
            loss = 0.0
            for line in result.stdout.split("\n"):
                if "平均" in line or "Average" in line or "rtt" in line.lower():
                    import re
                    nums = re.findall(r"[\d.]+", line)
                    if nums:
                        avg_ms = float(nums[-1])
                if "丢失" in line or "loss" in line.lower():
                    import re
                    nums = re.findall(r"[\d.]+", line)
                    if nums:
                        loss = float(nums[0])

            return {
                "host": host,
                "reachable": success,
                "avg_ms": avg_ms,
                "packet_loss": loss,
                "raw_output": result.stdout[:500],
            }
        except subprocess.TimeoutExpired:
            return {"host": host, "reachable": False, "avg_ms": 0, "packet_loss": 100, "error": "超时"}
        except FileNotFoundError:
            return {"host": host, "reachable": False, "avg_ms": 0, "packet_loss": 100, "error": "ping 命令不可用"}

    # =====================================================
    # 系统管理工具：环境变量
    # =====================================================

    def list_env_vars(self, pattern: str = None):
        """
        列出环境变量。

        参数：
            pattern: 过滤模式（子串匹配，不区分大小写）；未指定时列出全部

        返回：
            [{key, value}, ...]
        """
        result = []
        for key, value in os.environ.items():
            if pattern and pattern.lower() not in key.lower():
                continue
            # 敏感信息脱敏
            display_value = value
            for sensitive in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS"):
                if sensitive in key.upper():
                    display_value = "*** (已脱敏)"
                    break
            result.append({"key": key, "value": display_value})
        result.sort(key=lambda x: x["key"].lower())
        return result

    def get_env_var(self, name: str):
        """
        获取指定环境变量的值。

        参数：
            name: 环境变量名

        返回：
            {"name": str, "value": str, "exists": bool}
        """
        value = os.environ.get(name)
        exists = value is not None
        display = value if value is not None else ""
        # 敏感信息脱敏
        for sensitive in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS"):
            if sensitive in name.upper():
                display = "*** (已脱敏)" if value else ""
                break
        return {"name": name, "value": display, "exists": exists}
