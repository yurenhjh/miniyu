"""
mock_skills.py
第4组：Mock版 SkillLibrary（完整版）
提供所有系统技能的Mock实现，用于跨组联调
"""


class MockSkillLibrary:
    """Mock版 OS Skills 技能库"""

    def list_skills(self):
        return [
            "organize_downloads",
            "system_info",
            "cleanup_temp",
            "search_file",
            "find_large_files",
            "find_recent_files",
            "summarize_files",
            "cleanup_by_type",
            "batch_archive",
            "duplicate_finder",
            "rebuild_index",
            "query_index",
            "trash_file",
            "restore_file",
            "empty_trash",
            "safe_delete",
            "deduplicate_files",
            "backup_file",
            "restore_backup",
            "smart_organize",
            "app_open",
            "app_send_message",
            "read_qq_chat",
            "send_email",
            "browser_search",
            "browser_extract"
        ]

    def organize_downloads(self, path="~/Downloads"):
        return "[MOCK] 已整理下载目录: " + path + "（分类：文档/图片/压缩包）"

    def system_info(self):
        return {
            "system": "Linux (mock)",
            "release": "5.15.0 (mock)",
            "machine": "x86_64 (mock)",
            "python": "3.10.0 (mock)",
            "cpu": "Intel i7-12700 (mock)"
        }

    def cleanup_temp(self):
        return "[MOCK] 已清理临时文件，删除 42 个临时项目"

    def search_file(self, directory, keyword):
        return [
            directory + "/" + keyword + "_report.pdf",
            directory + "/notes/" + keyword + "_notes.txt"
        ]

    def find_large_files(self, parent, top=10):
        return [
            {"path": parent + "/big.bin", "size": 5000},
            {"path": parent + "/mid.txt", "size": 2000},
        ][:top]

    def find_recent_files(self, parent, days=7):
        return [parent + "/recent.txt", parent + "/docs/recent.log"]

    def summarize_files(self, parent):
        return {
            "file_count": 12,
            "dir_count": 3,
            "total_size": 65536,
            "ext_stats": {".txt": 5, ".png": 4, ".py": 3},
        }

    def cleanup_by_type(self, parent, extensions=(".tmp",)):
        return [parent + "/a.tmp", parent + "/b.tmp"]

    def batch_archive(self, items, dest_dir=None, action="compress"):
        if action == "extract":
            return {item: item + "_extracted" for item in items}
        return {item: item + "_archive.zip" for item in items}

    def duplicate_finder(self, parent):
        return [
            [{"path": parent + "/dup/a.txt", "size": 10},
             {"path": parent + "/dup/copy.txt", "size": 10}],
            [{"path": parent + "/dup2/b.txt", "size": 20},
             {"path": parent + "/dup2/b_copy.txt", "size": 20}],
        ]

    def rebuild_index(self, parent, path=None):
        if path is None:
            path = parent + "_index.json"
        return {"index_path": path, "count": 8}

    def query_index(self, path, keyword):
        return [
            {"path": path + "/" + keyword + "_report.pdf", "name": keyword + "_report.pdf",
             "type": "file", "size": 100, "mtime": 1000.0},
            {"path": path + "/notes/" + keyword + "_notes.txt", "name": keyword + "_notes.txt",
             "type": "file", "size": 50, "mtime": 900.0},
        ]

    # ---- 回收站 / 安全删除 ----

    def trash_file(self, path):
        return {"trashed_path": path + "_trashed", "original_path": path}

    def restore_file(self, path):
        return path.replace("_trashed", "")

    def empty_trash(self, confirm=False):
        return "[MOCK] 回收站已清空"

    def safe_delete(self, path, confirm=False):
        if not confirm:
            return {"requires_confirmation": True, "operation": "delete",
                    "affected_path": path}
        return self.trash_file(path)

    def deduplicate_files(self, directory, keep="newest", confirm=False):
        return {"removed": [f"{directory}/dup_b.txt"], "kept": [f"{directory}/dup_a.txt"],
                "groups": 1}

    # ---- 备份 / 恢复 ----

    def backup_file(self, path):
        return path + ".bak"

    def restore_backup(self, backup_path, output=None):
        return output or backup_path.replace(".bak", "")

    # ---- 智能整理 ----

    def smart_organize(self, directory, by="type"):
        return {"moved": [f"{directory}/图片/a.png"], "report": {"图片": 1}}

    # ---- 应用操作 ----

    def app_open(self, app_name):
        return {"window": {"hwnd": 101, "title": app_name, "pid": 1001}, "activated": True}

    def app_send_message(self, app_name, search_keyword, message,
                         verify=None, layout="qq_classic", max_result_rows=5,
                         verify_ocr=False, vision_js=None):
        return {
            "window": {"hwnd": 101, "title": app_name, "pid": 1001},
            "search_keyword": search_keyword,
            "message": message,
            "verify_ocr": bool(verify_ocr),
            "screenshot": "[MOCK] /tmp/mock_screenshot.png",
        }

    def read_qq_chat(self, app_name="QQ", search_keyword=None, max_lines=20,
                     verify=None, layout="qq_classic", max_result_rows=5,
                     verify_ocr=True, vision_js=None):
        return {
            "window": {"hwnd": 101, "title": app_name, "pid": 1001},
            "conversation": search_keyword,
            "max_lines": int(max_lines),
            "verify_result": bool(verify_ocr),
            "transcript": "[MOCK] 群友A: 你好\n群友B: 今晚一起吗？",
            "chars": 20,
            "screenshot": "[MOCK] /tmp/mock_qq_chat.png",
        }

    def send_email(self, to, subject, body, cc=None, verify=True):
        return {
            "to": to,
            "subject": subject,
            "cc": cc or "",
            "message_id": "[MOCK] <miniyu-mock@example.local>",
            "smtp_accepted": True,
            "sent_verified": True,
            "sent_verified_folder": "[MOCK] 已发送",
            "delivery_verified": True,
        }

    # ---- Agent 白名单技能（与正式版 SkillLibrary 接口对齐） ----

    # 与 core.skill_library._AGENT_SKILL_SCHEMAS 同源的白名单名（mock 只保证名字/形状）
    _AGENT_WHITELIST = ("app_send_message", "read_qq_chat",
                        "send_email", "browser_search", "browser_extract")

    def openai_skill_names(self):
        return [n for n in self._AGENT_WHITELIST if n in self.list_skills()]

    def is_agent_skill(self, name):
        return name in self._AGENT_WHITELIST and name in self.list_skills()

    def list_openai_tools(self):
        """返回 mock 白名单技能的 OpenAI function 描述（形状与正式版一致）"""
        _SCHEMAS = {
            "app_send_message": {
                "desc": "[MOCK] app_send_message：在 IM 应用内搜索会话并发送消息（含 OCR 核对）",
                "params": {
                    "type": "object",
                    "properties": {
                        "app_name": {"type": "string", "default": "QQ"},
                        "search_keyword": {"type": "string"},
                        "message": {"type": "string"},
                        "verify_ocr": {"type": "boolean", "default": True},
                    },
                    "required": ["search_keyword", "message"],
                },
            },
            "read_qq_chat": {
                "desc": "[MOCK] read_qq_chat：进入 QQ 指定会话读取最近聊天记录（只读，OCR 转写）",
                "params": {
                    "type": "object",
                    "properties": {
                        "app_name": {"type": "string", "default": "QQ"},
                        "search_keyword": {"type": "string"},
                        "max_lines": {"type": "integer", "default": 20},
                        "verify_ocr": {"type": "boolean", "default": True},
                    },
                    "required": ["search_keyword"],
                },
            },
            "send_email": {
                "desc": "[MOCK] send_email：发送邮件（SMTP + 自动 IMAP 回读核验）",
                "params": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                        "cc": {"type": "string"},
                        "verify": {"type": "boolean", "default": True},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            "browser_search": {
                "desc": "[MOCK] browser_search：浏览器联网搜索并返回结果摘要（bing/baidu）",
                "params": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "engine": {"type": "string", "enum": ["bing", "baidu"], "default": "bing"},
                    },
                    "required": ["query"],
                },
            },
            "browser_extract": {
                "desc": "[MOCK] browser_extract：打开网页 URL 读取正文文本",
                "params": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "selector": {"type": "string"},
                    },
                    "required": ["url"],
                },
            },
        }
        out = []
        for name in self.openai_skill_names():
            spec = _SCHEMAS[name]
            out.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["desc"],
                    "parameters": spec["params"],
                },
            })
        return out

    # ---- 浏览器结构化操作 ----

    def browser_search(self, query, engine="bing"):
        return {
            "query": query,
            "engine": engine,
            "url": f"[MOCK] https://www.{engine}.com/",
            "text_snippet": f"[MOCK] 关于 {query} 的搜索结果摘要",
        }

    def browser_extract(self, url, selector=None):
        return {
            "url": url,
            "selector": selector,
            "text": f"[MOCK] {url} 的正文内容",
        }
