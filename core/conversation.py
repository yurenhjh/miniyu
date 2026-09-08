"""
conversation.py
第4组：对话记忆管理

支持多轮对话的滑动窗口、消息序列化、标题生成、摘要压缩。
对标 ChatGPT / Claude / 豆包 的对话管理设计。
"""

import json
import re
import time
from typing import Optional


class Conversation:
    """
    对话记忆

    管理 messages 列表，每条消息包含 role / content / tool_calls 等字段。
    支持滑动窗口截取最近 N 条，防止 token 超限。

    消息格式对齐主流 LLM 的 chat API：
      - user:       {"role": "user", "content": "..."}
      - assistant:  {"role": "assistant", "content": "..."} 或带 tool_calls
      - tool:       {"role": "tool", "tool_call_id": "...", "content": "..."}
    """

    def __init__(self, window_size: int = 20):
        self.messages: list[dict] = []
        self.window_size = window_size
        self._tool_call_index = 0
        self.title = ""  # 会话标题（自动生成或用户自定义）
        self.summary = ""  # 对话摘要（用于记忆压缩，对标 AutoGPT）
        self.created_at = time.time()
        self.updated_at = time.time()

    def add_user(self, content: str):
        """添加用户消息"""
        self.messages.append({"role": "user", "content": content})
        self.updated_at = time.time()
        # 首条消息自动生成标题
        if not self.title and content:
            self._auto_title(content)

    def add_assistant(self, content: str = "", tool_calls: list = None):
        """
        添加助手消息。

        参数：
            content:   文本回复（纯文本回复时）
            tool_calls: LLM 返回的工具调用列表
                [{"name": "copy_file", "arguments": {"src": "a.txt", "dest": "b.txt"}}, ...]
        """
        msg = {"role": "assistant"}
        if content:
            msg["content"] = content
        if tool_calls:
            formatted = []
            for tc in tool_calls:
                self._tool_call_index += 1
                formatted.append({
                    "id": f"call_{self._tool_call_index}",
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]),
                    },
                })
            msg["tool_calls"] = formatted
            msg.setdefault("content", None)
        self.messages.append(msg)
        self.updated_at = time.time()

    def add_tool_result(self, tool_call_id: str, name: str, content: dict):
        """添加工具执行结果"""
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": json.dumps(content, ensure_ascii=False),
        })
        self.updated_at = time.time()

    def add_image(self, base64_data: str, mime_type: str = "image/png"):
        """添加图片消息（多模态）。将最后一条 user 消息改为多模态格式。"""
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i]["role"] == "user":
                original = self.messages[i]["content"]
                if isinstance(original, str):
                    self.messages[i]["content"] = [
                        {"type": "text", "text": original},
                        {"type": "image_url", "image_url": {
                            "url": f"data:{mime_type};base64,{base64_data}"
                        }},
                    ]
                elif isinstance(original, list):
                    original.append({"type": "image_url", "image_url": {
                        "url": f"data:{mime_type};base64,{base64_data}"
                    }})
                break

    def add_observation_image(self, note: str, base64_data: str, mime_type: str = "image/png"):
        """
        追加一条"截图观测"消息（多模态）：Agent 截屏后插入独立的一轮 user 消息，
        让视觉模型看到"当前界面长什么样"。note 是对这张图的文字说明。

        与 add_image 的区别：add_image 会把图塞到历史里最早那条 user 消息上（越堆越多）；
        观测消息则独立成一轮，语义清晰、可随滑动窗口自然淘汰。
        """
        self.messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": note or "（当前界面截图）"},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime_type};base64,{base64_data}"
                }},
            ],
        })
        self.updated_at = time.time()

    def get_window(self, n: int = None) -> list[dict]:
        """获取最近 N 条消息（滑动窗口）"""
        n = n or self.window_size
        return self.messages[-n:] if len(self.messages) > n else self.messages

    def get_last_tool_call_id(self) -> Optional[str]:
        """获取最后一条 tool_call 的 ID（用于回填结果）"""
        for msg in reversed(self.messages):
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                return msg["tool_calls"][0]["id"]
        return None

    def last_tool_call_ids(self) -> list:
        """
        返回最近一条助手 tool_calls 消息中的全部 tool_call id（按顺序）。

        多工具并行时，每条 tool 结果需回填到各自对应的 id（OpenAI 兼容 API 要求
        tool 消息的 tool_call_id 与上一条 assistant 消息里的某个 id 精确匹配）。
        """
        for msg in reversed(self.messages):
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                return [tc["id"] for tc in msg["tool_calls"]]
        return []

    def get_last_tool_calls(self) -> list:
        """获取最后一条助手消息中的工具调用列表"""
        for msg in reversed(self.messages):
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                calls = []
                for tc in msg["tool_calls"]:
                    calls.append({
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "arguments": json.loads(tc["function"]["arguments"]),
                    })
                return calls
        return []

    def clear(self):
        """清空对话历史"""
        self.messages = []
        self.title = ""
        self.summary = ""
        self._tool_call_index = 0

    @property
    def total_messages(self) -> int:
        """获取消息总数"""
        return len(self.messages)

    @property
    def total_tokens_estimate(self) -> int:
        """估算总 token 数"""
        total = 0
        for msg in self.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 2
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        total += len(item.get("text", "")) // 2
            # tool_calls 也计入
            for tc in msg.get("tool_calls", []):
                func = tc.get("function", {})
                total += len(func.get("name", "")) // 2
                total += len(func.get("arguments", "")) // 2
        return total

    # ============================================================
    # 标题生成
    # ============================================================

    def _auto_title(self, first_message: str):
        """从首条用户消息自动生成会话标题。

        策略：取第一句（首个停顿标点前的内容）做标题，更像"摘要"而非硬切；
        超过 20 字符截断加 …；不依赖 LLM（离线/确定性脑也能用）。
        """
        text = first_message.strip().replace("\r", " ").replace("\n", " ")
        # 首个句子停顿标点前的内容（。！？，；、：…等，含半角）
        cut = re.split(r"[。！？!?，,；;、：:…]", text, maxsplit=1)[0].strip()
        title = cut or text
        if len(title) > 20:
            title = title[:20] + "…"
        self.title = title

    def set_title(self, title: str):
        """用户自定义标题"""
        self.title = title.strip()[:50]

    # ============================================================
    # 摘要压缩（类似 LangChain ConversationSummaryBufferMemory）
    # ============================================================

    def get_summarized_window(self, n: int = None, summary_fn=None) -> list[dict]:
        """
        获取带摘要的滑动窗口。

        当消息超过窗口大小时，将旧消息压缩为一条摘要消息。
        对标 LangChain 的 ConversationSummaryBufferMemory 设计。

        参数：
            n:           窗口大小（默认 window_size）
            summary_fn:  摘要生成函数（用于真实 LLM 摘要）
                         离线模式下用简单截断

        返回：
            [摘要消息, ...最近 N 条消息]
        """
        n = n or self.window_size
        if len(self.messages) <= n:
            return self.messages

        # 需要压缩的消息（旧消息）
        old_messages = self.messages[:-n]
        recent_messages = self.messages[-n:]

        # 生成摘要
        if summary_fn:
            summary = summary_fn(old_messages)
        else:
            summary = self._simple_summary(old_messages)

        summary_msg = {
            "role": "system",
            "content": f"以下是历史对话摘要（已压缩）：\n{summary}\n\n请基于此摘要和后续消息继续对话。",
        }

        return [summary_msg] + recent_messages

    @staticmethod
    def _simple_summary(messages: list[dict]) -> str:
        """简单摘要：提取关键信息"""
        parts = []
        for msg in messages:
            role = msg["role"]
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                if role == "user":
                    parts.append(f"用户说：{content[:100]}")
                elif role == "assistant":
                    parts.append(f"助手回复：{content[:100]}")
                elif role == "tool":
                    parts.append(f"工具执行：{msg.get('name', '?')}")
        return "\n".join(parts[-10:])  # 最多保留 10 条摘要

    # ============================================================
    # 序列化
    # ============================================================

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "title": self.title,
            "summary": self.summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "window_size": self.window_size,
            "tool_call_index": self._tool_call_index,
            "messages": self.messages,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Conversation":
        """从字典恢复"""
        conv = cls(window_size=data.get("window_size", 20))
        conv.messages = data.get("messages", [])
        conv.title = data.get("title", "")
        # 迁移：旧版默认标题"新对话"（或空）→ 用首条用户消息重生成，让历史侧栏可辨识
        if not conv.title or conv.title == "新对话":
            first_user = next(
                (m.get("content") for m in conv.messages
                 if m.get("role") == "user" and m.get("content")),
                "",
            )
            if first_user:
                conv._auto_title(first_user)
        conv.summary = data.get("summary", "")
        conv.created_at = data.get("created_at", time.time())
        conv.updated_at = data.get("updated_at", time.time())
        conv._tool_call_index = data.get("tool_call_index", 0)
        return conv

    def to_json(self, indent=2) -> str:
        """序列化为 JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_json(cls, json_str: str) -> "Conversation":
        """从 JSON 恢复"""
        return cls.from_dict(json.loads(json_str))

    def delete_last_turn(self):
        """
        删除最后一轮对话（用户消息 + 对应的助手回复）。
        对标 ChatGPT 的"编辑消息后删除后续"功能。
        """
        if not self.messages:
            return
        # 从后往前删除直到遇到 user 消息
        while self.messages and self.messages[-1]["role"] != "user":
            self.messages.pop()
        # 删除最后一条 user 消息
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()
        self.updated_at = time.time()


class SessionManager:
    """
    多会话管理器

    管理多个 Conversation 实例，支持：
      - 创建/删除/切换/重命名会话
      - 持久化到磁盘（JSON 文件）
      - 会话列表/搜索/自动保存

    对标 ChatGPT / Claude / Open WebUI 的会话管理设计。
    """

    def __init__(self, storage_dir: str = "conversations"):
        self.storage_dir = storage_dir
        self._sessions: dict[str, Conversation] = {}  # id -> Conversation
        self._current_id: Optional[str] = None
        self._ensure_storage_dir()

    def _ensure_storage_dir(self):
        import os
        os.makedirs(self.storage_dir, exist_ok=True)

    @property
    def current(self) -> Optional[Conversation]:
        """获取当前会话"""
        if self._current_id and self._current_id in self._sessions:
            return self._sessions[self._current_id]
        return None

    @property
    def current_id(self) -> Optional[str]:
        return self._current_id

    # ============================================================
    # 会话 CRUD
    # ============================================================

    def create(self, title: str = "") -> str:
        """
        创建新会话。
        对标 ChatGPT 的 "New Chat" 功能。
        标题默认留空：第一条用户消息到达时由 _auto_title 自动生成
        （用首句话做标题，侧栏在首条消息前显示"新对话"兜底）。
        """
        import uuid
        session_id = str(uuid.uuid4())[:8]
        conv = Conversation()
        conv.title = title
        self._sessions[session_id] = conv
        self._current_id = session_id
        self._save(session_id)
        return session_id

    def switch(self, session_id: str) -> bool:
        """切换到指定会话（刷新其"最近访问"时间，历史侧栏按此排序靠前）"""
        if session_id in self._sessions:
            self._current_id = session_id
            self._sessions[session_id].updated_at = time.time()
            self._save(session_id)
            return True
        return False

    def delete(self, session_id: str) -> bool:
        """删除会话（保留文件，标记为已删除）"""
        if session_id not in self._sessions:
            return False
        del self._sessions[session_id]
        # 删除文件
        import os
        path = os.path.join(self.storage_dir, f"{session_id}.json")
        if os.path.exists(path):
            os.remove(path)
        # 如果删除的是当前会话，切换到最近的会话
        if self._current_id == session_id:
            ids = self.list_ids()
            self._current_id = ids[0] if ids else None
        return True

    def rename(self, session_id: str, title: str) -> bool:
        """重命名会话"""
        if session_id not in self._sessions:
            return False
        self._sessions[session_id].set_title(title)
        self._save(session_id)
        return True

    def list_ids(self) -> list[str]:
        """获取所有会话 ID（按更新时间降序）"""
        ids = list(self._sessions.keys())
        ids.sort(key=lambda x: self._sessions[x].updated_at, reverse=True)
        return ids

    def list(self) -> list[dict]:
        """获取会话列表摘要"""
        result = []
        for sid in self.list_ids():
            conv = self._sessions[sid]
            result.append({
                "id": sid,
                "title": conv.title,
                "message_count": conv.total_messages,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at,
                "is_current": sid == self._current_id,
            })
        return result

    # ============================================================
    # 持久化
    # ============================================================

    def _save(self, session_id: str):
        """保存单个会话到磁盘"""
        if session_id not in self._sessions:
            return
        import os
        conv = self._sessions[session_id]
        path = os.path.join(self.storage_dir, f"{session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(conv.to_json())

    def save_all(self):
        """保存所有会话"""
        for sid in self._sessions:
            self._save(sid)

    def save(self, session_id: str = None):
        """保存指定会话（默认当前）"""
        self._save(session_id or self._current_id)

    def load(self, session_id: str) -> Optional[Conversation]:
        """从磁盘加载单个会话"""
        import os
        path = os.path.join(self.storage_dir, f"{session_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            conv = Conversation.from_json(f.read())
        self._sessions[session_id] = conv
        return conv

    def load_all(self):
        """从磁盘加载所有会话"""
        import os
        self._sessions = {}
        if not os.path.exists(self.storage_dir):
            return
        for fname in os.listdir(self.storage_dir):
            if fname.endswith(".json"):
                sid = fname[:-5]
                try:
                    self.load(sid)
                except Exception:
                    continue
        # 设置当前会话为最近的一个
        ids = self.list_ids()
        self._current_id = ids[0] if ids else None

    def ensure_current(self) -> Conversation:
        """确保存在当前会话（没有则创建）"""
        if self.current is None:
            self.create()
        return self.current

    def get_title_suggestions(self):
        """生成标题建议（供用户选择）"""
        return ["新对话", "文件操作", "系统管理", "浏览器操作", "综合任务"]