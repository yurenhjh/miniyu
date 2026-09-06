"""
main.py
第4组 Demo入口 — 展示工具注册表、技能库、统计功能
兼容 Windows / Ubuntu 双系统
"""

import shutil
from pathlib import Path

from core.os_service_api import OSServiceAPI
from core.utils import safe_print

api = OSServiceAPI()

# 清理上一次运行残留的 Demo 产物，保证可重复运行
for _p in ("index_demo.json", "archive_demo.zip", "archive_demo_extracted"):
    _pt = Path(_p)
    if _pt.is_dir():
        shutil.rmtree(_pt, ignore_errors=True)
    elif _pt.exists():
        _pt.unlink()

safe_print("=" * 50)
safe_print("Group4 - Tool Registry + OS Skills Demo")
safe_print("=" * 50)

# 展示可用工具
tools = api.list_available_tools()
safe_print(f"\nAvailable tools ({len(tools)}):")
for t in tools:
    safe_print(f"  - {t}")

# 展示可用技能
skills = api.list_available_skills()
safe_print(f"\nAvailable skills ({len(skills)}):")
for s in skills:
    safe_print(f"  - {s}")

# 调用示例
safe_print("\n" + "-" * 50)
safe_print("Tool call demo:")
safe_print("-" * 50)

result = api.execute_tool("create_directory", {"path": "./demo_dir"})
safe_print(f"create_directory: {result['success']} | {result['result']}")

result = api.execute_tool("list_directory", {"path": "."})
safe_print(f"list_directory: {result['success']} | {len(result['result'])} items")

result = api.execute_tool("current_directory", {})
safe_print(f"current_directory: {result['success']} | {result['result']}")

result = api.execute_tool("run_command", {"cmd": "echo Hello Agentic OS"})
safe_print(f"run_command: {result['success']} | {result['result']['stdout'].strip()}")

result = api.execute_tool("sort_directory", {
    "path": ".",
    "sort_by": "size",
    "reverse": True
})
safe_print(f"sort_directory: {result['success']} | "
           f"{len(result['result'])} items, "
           f"top = {result['result'][0]['name'] if result['result'] else '-'}")

result = api.execute_tool("index_files", {"path": ".", "persist": "./index_demo.json"})
safe_print(f"index_files: {result['success']} | {len(result['result'])} entries "
           f"(persisted to ./index_demo.json)")
result = api.execute_tool("index_files", {"path": ".", "load": "./index_demo.json"})
safe_print(f"index_files(load): {result['success']} | loaded {len(result['result'])} entries")

result = api.execute_tool("manage_archive", {"action": "compress", "src_dir": "./docs", "dest_zip": "./archive_demo"})
safe_print(f"manage_archive(compress): {result['success']} | {result['result']}")

_result = api.execute_tool("manage_archive", {"action": "extract", "dest_zip": result["result"]})
safe_print(f"manage_archive(extract): {_result['success']} | {_result['result']}")

# 新工具演示（元数据 / 校验和 / 磁盘 / 空文件查找）
result = api.execute_tool("get_path_metadata", {"path": "./README.md"})
safe_print(f"get_path_metadata: {result['success']} | "
           f"size={result['result']['size']}, ext={result['result']['extension']}")

result = api.execute_tool("file_checksum", {"path": "./README.md", "algorithm": "sha256"})
safe_print(f"file_checksum: {result['success']} | {result['result']['checksum'][:16]}...")

result = api.execute_tool("disk_usage", {"path": "."})
safe_print(f"disk_usage: {result['success']} | used {result['result']['percent_used']}%")

result = api.execute_tool("find_empty_files", {"directory": "./docs"})
safe_print(f"find_empty_files: {result['success']} | {len(result['result'])} empty files")

# 技能调用示例
safe_print("\n" + "-" * 50)
safe_print("Skill call demo:")
safe_print("-" * 50)

result = api.run_skill("system_info")
safe_print(f"system_info: {result['success']}")
info = result['result']
safe_print(f"  OS: {info['system']} {info['release']}")
safe_print(f"  Arch: {info['machine']}")
safe_print(f"  Python: {info['python']}")

# 扩展技能演示
result = api.run_skill("find_large_files", {"parent": "./docs", "top": 3})
safe_print(f"find_large_files: {result['success']} | top3 sizes = "
           f"{[e['size'] for e in result['result']]}")

result = api.run_skill("summarize_files", {"parent": "./docs"})
safe_print(f"summarize_files: {result['success']} | "
           f"{result['result']['file_count']} files, "
           f"{result['result']['total_size']} bytes")

# Agent 组合技能演示（回收站 / 安全删除 / 备份）
_tmp = "./demo_tmp.txt"
with open(_tmp, "w") as f:
    f.write("temporary demo file")

result = api.run_skill("safe_delete", {"path": _tmp})
safe_print(f"safe_delete(preview): {result['success']} | requires_confirmation={result['result']['requires_confirmation']}")

result = api.run_skill("trash_file", {"path": _tmp})
safe_print(f"trash_file: {result['success']} | trashed={result['result']['trashed_path']}")

# 清空回收站（强化确认演示）
result = api.run_skill("empty_trash", {"confirm": True})
safe_print(f"empty_trash: {result['success']} | {result['result']}")

# 搜索模式演示（精确 / 通配符 / 正则）
result = api.run_skill("search_file", {"directory": "./docs", "keyword": "*.md", "mode": "wildcard"})
safe_print(f"search_file(wildcard *.md): {result['success']} | found {len(result['result'])}")

result = api.run_skill("search_file", {"directory": "./docs/adr", "keyword": r"000\d.*\.md", "mode": "regex"})
safe_print(f"search_file(regex ADR): {result['success']} | found {len(result['result'])} ADR")

# 统计功能
safe_print("\n" + "-" * 50)
safe_print("Call statistics:")
safe_print("-" * 50)

stats = api.get_tool_stats()
safe_print(f"Total calls: {stats['total_calls']}")
safe_print(f"Success rate: {stats['success_rate']}%")
safe_print(f"By tool: {stats['by_tool']}")

safe_print("\n" + "=" * 50)
safe_print("Demo completed [OK]")
safe_print("=" * 50)
