"""
llm_client.py
第4组：LLM 客户端抽象层

支持三种模式：
  1. DeterministicBrain  — 离线规则匹配，零依赖，默认兜底
  2. OpenAICompatibleClient — 兼容 OpenAI 格式的任意 API（GPT / DeepSeek / 豆包 / Ollama）
  3. ChatResponse — 统一响应格式

设计参考：
  - OpenAI Function Calling 格式
  - Anthropic Tool Use 格式
  - LangChain @tool 装饰器的参数生成思路
"""

import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

import requests


# ============================================================
# 统一响应格式
# ============================================================

@dataclass
class ChatResponse:
    """
    LLM 统一响应

    text:       纯文本回复（finish_reason=stop 时）
    tool_calls: 工具调用列表（finish_reason=tool_calls 时）
                 每项格式：{"name": str, "arguments": dict}
    finish_reason: "stop" | "tool_calls"
    raw:        原始响应（调试用）
    """
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    raw: Optional[dict] = None

    def __bool__(self):
        return bool(self.text) or bool(self.tool_calls)


# ============================================================
# 抽象基类
# ============================================================

class LLMClient(ABC):
    """LLM 客户端抽象基类"""

    supports_vision: bool = False
    provider: str = "abstract"

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] = None) -> ChatResponse:
        """
        发送对话消息，返回响应。

        参数：
            messages: 消息列表，格式同 OpenAI Chat API
                      [{"role": "user", "content": "..."}, ...]
            tools:    工具描述列表（OpenAI Function Calling 格式）
                      [{"type": "function", "function": {...}}, ...]
        返回：
            ChatResponse
        """

    def count_tokens(self, messages: list[dict]) -> int:
        """估算 token 数（子类可重写精确计算）"""
        return sum(len(str(m.get("content", ""))) // 2 for m in messages)

    def chat_stream(self, messages: list[dict], tools: list[dict] = None):
        """
        流式聊天：逐 token 产出 ChatResponse。

        默认实现直接 yield chat() 的结果，子类可重写为真正的 SSE 流式。
        对标 ChatGPT 的逐字输出效果。
        """
        yield self.chat(messages, tools=tools)


# ============================================================
# 离线脑：基于规则匹配，零依赖
# ============================================================

class DeterministicBrain(LLMClient):
    """
    离线确定性脑

    通过关键词匹配将用户输入映射到预定义的 Plan。
    零依赖、零网络、零模型，确保在任何环境下都能演示。

    设计参考：
      - 微软 UFO² 的 HostAgent._parse_teammate_response
      - 规则兜底思路：先用关键词匹配，无匹配时提示可用指令
    """

    provider = "deterministic"
    supports_vision = False

    def __init__(self):
        # 意图列表：(匹配关键词列表, 工具调用列表, 纯文本回复)
        self._intents = [
            # ---- 文件操作 ----
            (["桌面", "整理", "归类", "分类"], [
                {
                    "name": "sort_directory",
                    "arguments": {"path": "~/Desktop", "sort_by": "type"},
                },
            ], None),

            (["下载", "download"], [
                {
                    "name": "sort_directory",
                    "arguments": {"path": "~/Downloads", "sort_by": "type"},
                },
            ], None),

            (["创建", "目录", "mkdir"], [
                {
                    "name": "create_directory",
                    "arguments": {"path": ""},
                },
            ], "请告诉我目录名称，例如：创建文件夹 工作文档"),

            (["删除", "删掉", "移除"], [
                {
                    "name": "delete_path",
                    "arguments": {"path": ""},
                },
            ], "请告诉我要删除的文件路径。注意：此操作不可撤销！"),

            (["复制", "拷贝"], [
                {
                    "name": "copy_file",
                    "arguments": {"src": "", "dest": ""},
                },
            ], "请告诉我源路径和目标路径，例如：复制 C:\\a.txt D:\\b.txt"),

            (["移动", "剪切", "mv"], [
                {
                    "name": "move_path",
                    "arguments": {"src": "", "dest": ""},
                },
            ], "请告诉我源路径和目标路径，例如：移动 C:\\a.txt D:\\"),

            (["重命名", "改名"], [
                {
                    "name": "rename_path",
                    "arguments": {"path": "", "new_name": ""},
                },
            ], "请告诉我文件路径和新名称，例如：重命名 C:\\a.txt b.txt"),

            (["查找", "搜索", "找", "find", "文件夹"], [
                {
                    "name": "search_files",
                    "arguments": {"pattern": "", "path": "~"},
                },
            ], "请告诉我搜索关键词，例如：查找 *.pdf、查找 报告、或帮我找一下名为'好'的文件夹"),

            (["大文件", "大文档", "占空间"], [
                {
                    "name": "find_large_files",
                    "arguments": {"directory": "C:\\", "min_size_mb": 100, "limit": 20},
                },
            ], None),

            (["磁盘", "C盘", "D盘", "空间", "容量"], [
                {
                    "name": "disk_usage",
                    "arguments": {"path": "C:\\"},
                },
            ], None),

            # ---- 进程管理 ----
            (["进程", "任务", "卡顿", "慢", "cpu"], [
                {
                    "name": "list_processes",
                    "arguments": {"sort_by": "cpu", "limit": 10},
                },
            ], None),

            (["杀", "结束", "结束进程", "kill", "关闭进程"], [
                {
                    "name": "kill_process",
                    "arguments": {"pid": 0},
                },
            ], "请告诉我进程 PID 或名称，例如：结束进程 1234"),

            # ---- 网络 ----
            (["网络", "网", "连接", "ping", "ip"], [
                {
                    "name": "network_status",
                    "arguments": {},
                },
            ], None),

            (["ping"], [
                {
                    "name": "ping_host",
                    "arguments": {"host": "baidu.com"},
                },
            ], None),

            # ---- 环境变量 ----
            (["环境变量", "env", "PATH"], [
                {
                    "name": "list_env_vars",
                    "arguments": {},
                },
            ], None),

            # ---- 列出工具 ----
            (["工具", "你能", "功能", "help", "帮助", "支持", "指令"], None,
             '当前为离线模式，支持以下指令：\n\n'
             '📁 文件操作：\n'
             '  · 整理桌面 — 按类型分类桌面文件\n'
             '  · 下载文件 — 整理下载文件夹\n'
             '  · 查找文件 — 按名称搜索文件\n'
             '  · 查找大文件 — 查找 C 盘大于 100MB 的文件\n'
             '  · 磁盘空间 — 查看磁盘使用情况\n'
             '  · 创建/删除/复制/移动/重命名 — 基础文件操作\n\n'
             '⚙️ 系统管理：\n'
             '  · 查看进程 — 查看 CPU 占用最高的进程\n'
             '  · 网络状态 — 查看网络连接信息\n'
             '  · 环境变量 — 查看系统环境变量\n\n'
             '🖥️ 应用操作：\n'
             '  · 打开应用 — 启动指定软件\n'
             '  · 截图 — 截取当前屏幕\n'
             '  · 浏览器 — 打开浏览器\n\n'
             '💡 如需处理更复杂的任务（如「帮我找一下名为报告的文件」），'
             '请配置 API key 使用真实 LLM。\n'
             '  编辑 config.yaml 设置 provider: openai_compatible'),

            # ---- 应用操作 ----
            (["打开", "启动", "运行"], [
                {
                    "name": "open_application",
                    "arguments": {"app_name": ""},
                },
            ], "请告诉我要打开的应用名称，例如：打开 计算器"),

            (["截图", "screenshot", "截屏"], [
                {
                    "name": "take_screenshot",
                    "arguments": {},
                },
            ], None),

            (["浏览器", "edge", "chrome"], [
                {
                    "name": "open_browser",
                    "arguments": {"url": "https://www.baidu.com"},
                },
            ], None),
        ]

        # 通用回复（无匹配时）
        self._fallback = (
            '我不太理解这个指令。\n\n'
            '当前为离线模式，我只能执行以下预设指令：\n'
            '  · 整理桌面 / 磁盘空间 / 查看进程\n'
            '  · 网络状态 / 查找大文件 / 环境变量\n'
            '  · 创建/删除/复制/移动文件\n'
            '  · 打开应用 / 浏览器\n\n'
            '如需处理更复杂的任务（如"帮我找一下名为报告的文件"），'
            '请配置 API key 使用真实 LLM：\n'
            '  编辑 config.yaml，设置 provider: openai_compatible\n'
            '  或设置环境变量 AGENT_LLM_PROVIDER=openai_compatible\n'
            '  输入「帮助」查看完整指令列表'
        )

    def chat(self, messages: list[dict], tools: list[dict] = None) -> ChatResponse:
        """关键词匹配"""
        # 取最后一条用户消息
        user_text = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                content = msg["content"]
                if isinstance(content, list):
                    for item in content:
                        if item.get("type") == "text":
                            user_text += item["text"]
                else:
                    user_text = str(content)
                break

        if not user_text:
            return ChatResponse(text="请说点什么？")

        user_text_lower = user_text.lower()

        # 按匹配得分排序：优先匹配更长（更具体）的关键词
        best_score = -1
        best_match = None
        best_keyword = ""

        for keywords, tool_calls, reply_text in self._intents:
            for kw in keywords:
                if kw in user_text_lower:
                    score = len(kw)
                    if score > best_score:
                        best_score = score
                        best_match = (tool_calls, reply_text)
                        best_keyword = kw

        if best_match is None:
            return ChatResponse(text=self._fallback)

        tool_calls, reply_text = best_match

        if tool_calls:
            filled_calls = self._fill_arguments(tool_calls, user_text)
            if filled_calls:
                return ChatResponse(
                    tool_calls=filled_calls,
                    finish_reason="tool_calls",
                )
        if reply_text:
            return ChatResponse(text=reply_text)
        return ChatResponse(text=self._fallback)

    def _fill_arguments(self, tool_calls: list[dict], user_text: str) -> list[dict]:
        """从用户输入中提取并填充参数"""
        filled = []
        has_missing = False

        for tc in tool_calls:
            args = dict(tc["arguments"])

            # 尝试从用户输入提取路径
            for key in ("path", "src", "dest", "new_name"):
                if key in args and not args[key]:
                    extracted = self._extract_path(user_text)
                    if extracted:
                        args[key] = extracted
                    else:
                        # 文件路径参数是必需的，留空则标记为缺失
                        if key in ("path", "src", "dest"):
                            has_missing = True

            # 提取搜索关键词（pattern）
            if "pattern" in args and not args["pattern"]:
                extracted = self._extract_search_pattern(user_text)
                if extracted:
                    args["pattern"] = extracted
                else:
                    has_missing = True

            # 尝试提取 PID 或进程名
            if "pid" in args and args["pid"] == 0:
                pid_match = re.search(r"(\d{2,})", user_text)
                if pid_match:
                    args["pid"] = int(pid_match.group(1))
                else:
                    has_missing = True

            # 尝试提取应用名
            if "app_name" in args and not args["app_name"]:
                for word in user_text.split():
                    if word not in ("打开", "启动", "运行", "open", "launch", "run"):
                        args["app_name"] = word
                        break
                if not args["app_name"]:
                    has_missing = True

            filled.append({"name": tc["name"], "arguments": args})

        # 有关键参数缺失时返回空列表，让 chat 方法走回复文本
        if has_missing:
            return []
        return filled

    @staticmethod
    def _extract_search_pattern(text: str) -> str:
        """从用户输入中提取搜索关键词"""
        # 尝试匹配 "名为'xxx'" 或 "名为"xxx""
        m = re.search(r"名为[‘'\"]([^’'\"\s]+)[’'\"]", text)
        if m:
            return m.group(1)
        # 尝试匹配 "叫xxx" 或 "找xxx" 后的词
        m = re.search(r"(?:叫|找|搜)[：: ]?\s*([\u4e00-\u9fff\w.]+)", text)
        if m:
            return m.group(1)
        # 尝试匹配文件名模式
        m = re.search(r"[\w*?.]+\.[\w]{2,4}", text)
        if m:
            return m.group(0)
        # 取最后一个有意义的词（去除停用词）
        stop_words = {"帮我", "一下", "一个", "文件", "文件夹", "目录", "的"}
        words = [w for w in text.split() if w not in stop_words and len(w) > 1]
        if words:
            return words[-1]
        return ""

    def chat_stream(self, messages: list[dict], tools: list[dict] = None):
        """离线脑的流式：直接 yield 完整结果"""
        yield self.chat(messages, tools=tools)

    @staticmethod
    def _extract_path(text: str) -> str:
        """从文本中提取路径"""
        # 匹配 Windows 路径 C:\... 或 ~/...
        patterns = [
            r"[A-Za-z]:\\(?:[^\\s]+\\)*[^\\s]*",  # C:\Users\...
            r"~[/\\][^\\s]*",                       # ~/Desktop
            r"\.\.[/\\][^\\s]*",                    # ..\a.txt
            r"\.\\[^\\s]*|\./[^\\s]*",              # .\a.txt
        ]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                return match.group(0)
        return ""


# ============================================================
# OpenAI 兼容客户端（GPT / DeepSeek / 豆包 / Ollama 等）
# ============================================================

class OpenAICompatibleClient(LLMClient):
    """
    兼容 OpenAI Chat Completions API 的任意 LLM。

    支持的提供商：
      - OpenAI（GPT-4o, GPT-4o-mini）
      - DeepSeek
      - 火山方舟（豆包）
      - Ollama（本地模型）
      - 任何兼容 /v1/chat/completions 的 API

    超时与 token 耗尽处理：
      - 每次请求有独立超时（默认 60s）
      - 自动检测 token 用量，累计接近限额时发出警告
      - 常见错误（认证失败、额度耗尽、限流等）返回清晰的中文提示
    """

    provider = "openai_compatible"

    # 常见 HTTP 错误码 → 中文提示
    _ERROR_TIPS = {
        401: ("认证失败", "API key 无效或已过期。请检查 config.yaml 中的 api_key 配置。"),
        402: ("额度耗尽", "API 余额不足或 token 配额已用完。请检查账户余额或充值。"),
        403: ("权限不足", "API key 没有访问该模型的权限。请检查模型名称配置。"),
        429: ("请求过频", "API 请求频率过高，已自动等待后重试。请稍后再试。"),
        500: ("服务端错误", "LLM 服务端内部错误，请稍后重试。"),
    }

    def __init__(
        self,
        base_url: str = "",
        api_key: str = "",
        model: str = "",
        supports_vision: bool = False,
        timeout: int = 60,
        max_total_tokens: int = 0,
    ):
        # base_url 归一化：缺 /v1 自动补（如 https://api.deepseek.com → .../v1），
        # 让不同提供商的写法和 README/config 注释保持一致。
        base_url = base_url.rstrip("/")
        if not base_url:
            base_url = "https://api.openai.com/v1"
        elif not base_url.endswith("/v1"):
            base_url += "/v1"
        self.base_url = base_url
        self.api_key = api_key
        self.model = model or "gpt-4o-mini"
        self.supports_vision = supports_vision
        self.timeout = timeout
        self.max_total_tokens = max_total_tokens  # 0 表示不限制
        self.total_tokens_used = 0
        self._last_usage = {}

    @property
    def token_usage(self) -> dict:
        """返回当前会话的 token 用量统计"""
        return {
            "total_used": self.total_tokens_used,
            "max_allowed": self.max_total_tokens,
            "last_request": self._last_usage,
        }

    def _check_token_budget(self, estimated: int) -> bool:
        """检查 token 预算，返回 True 表示还有余量"""
        if self.max_total_tokens <= 0:
            return True
        remaining = self.max_total_tokens - self.total_tokens_used
        if remaining < estimated:
            return False
        return True

    def chat(self, messages: list[dict], tools: list[dict] = None) -> ChatResponse:
        """调用 OpenAI 兼容 API"""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }

        # token 预算检查
        estimated = sum(len(str(m.get("content", ""))) for m in messages) // 2
        if not self._check_token_budget(estimated + 4096):
            return ChatResponse(
                text="⚠️ API token 配额已用尽。\n\n"
                     f"本会话已使用 {self.total_tokens_used} tokens，"
                     f"达到上限 {self.max_total_tokens}。\n"
                     "请重置对话（/reset）或提高 max_total_tokens 配置。"
            )

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        try:
            resp = requests.post(
                url, headers=headers, json=body, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError:
            msg = (f"无法连接到 {self.base_url}，请检查网络或 base_url 配置。\n\n"
                   f"提示：阿里云 DashScope 的 base_url 为：\n"
                   f"  https://dashscope.aliyuncs.com/compatible-mode/v1\n"
                   f"DeepSeek 的 base_url 为：\n"
                   f"  https://api.deepseek.com")
            return ChatResponse(text=msg)
        except requests.exceptions.Timeout:
            return ChatResponse(
                text=f"⏱️ 请求超时（{self.timeout}s）。\n\n"
                     "可能原因：\n"
                     "  1. 网络连接较慢，请稍后重试\n"
                     "  2. API token 额度已用完，请求被拒绝\n"
                     "  3. 模型负载过高，响应较慢\n\n"
                     "建议：在 config.yaml 中增大 timeout 值，或检查账户余额。"
            )
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            detail = ""
            if e.response is not None:
                try:
                    detail = e.response.json().get("error", {}).get("message", "")
                except Exception:
                    detail = e.response.text[:200]

            # 匹配已知错误码
            if status in self._ERROR_TIPS:
                title, tip = self._ERROR_TIPS[status]
                return ChatResponse(
                    text=f"❌ {title}（HTTP {status}）\n\n{tip}\n\n详细信息：{detail}"
                )

            # 401 但也可能是 key 格式问题
            if "auth" in detail.lower() or "key" in detail.lower() or "token" in detail.lower():
                return ChatResponse(
                    text=f"❌ 认证失败（HTTP {status}）\n\n"
                         "API key 无效或格式不正确。请检查 config.yaml 中的配置。\n\n"
                         f"详细信息：{detail}"
                )

            # 额度耗尽检测（关键词）
            if any(kw in detail.lower() for kw in ("quota", "insufficient", "exhausted", "limit", "额度", "余额", "用量")):
                return ChatResponse(
                    text=f"❌ API 额度耗尽\n\n"
                         f"API 返回：{detail}\n\n"
                         "建议：\n"
                         "  1. 检查账户余额是否充足\n"
                         "  2. token 配额是否已用完\n"
                         "  3. 重置对话后使用更小的模型"
                )

            return ChatResponse(
                text=f"❌ API 请求失败（HTTP {status}）\n\n{detail}\n\n"
                     "请检查网络连接和 API 配置。"
            )
        except Exception as e:
            return ChatResponse(text=f"请求异常：{e}")

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")

        # 记录 token 用量
        usage = data.get("usage", {})
        if usage:
            self._last_usage = usage
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)
            self.total_tokens_used += total_tokens

            # token 用量接近上限时警告
            if self.max_total_tokens > 0:
                remaining = self.max_total_tokens - self.total_tokens_used
                if remaining < 5000:
                    pass  # 会通过 _check_token_budget 在下次请求时拦截

        # 解析 tool_calls
        tool_calls = []
        raw_tcs = message.get("tool_calls", [])
        for tc in raw_tcs:
            func = tc.get("function", {})
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            tool_calls.append({
                "name": func.get("name", ""),
                "arguments": args,
            })

        content = message.get("content", "") or ""
        # 后端不支持原生 tool_calls、只回 JSON 文本时的兜底
        if not tool_calls and content:
            parsed = self._parse_json_toolcalls(content)
            if parsed:
                tool_calls = parsed
                finish_reason = "tool_calls"

        return ChatResponse(
            text=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            raw=data,
        )

    @staticmethod
    def _parse_json_toolcalls(text: str):
        """
        把纯文本里的 function-call JSON 解析成 tool_calls（兜底，容忍围栏）。

        支持两种形状：
          - 单对象： {"name": "create_directory", "arguments": {...}}
          - 数组：   [{"name": ..., "arguments": {...}}, ...]
        解析失败返回 None，保持原文本。
        """
        s = (text or "").strip()
        if not s:
            return None
        # 去掉 ```json ... ``` 围栏
        if s.startswith("```"):
            s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
            s = re.sub(r"\s*```\s*$", "", s)
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            return None

        items = obj if isinstance(obj, list) else [obj]
        tool_calls = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name") or it.get("tool")
            func = it.get("function")
            if isinstance(func, dict):
                name = func.get("name") or name
            if not isinstance(name, str) or not name:
                continue
            args = it.get("arguments")
            if isinstance(func, dict) and args is None:
                args = func.get("arguments")
            if args is None:
                args = it.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append({"name": name, "arguments": args})
        return tool_calls or None

    def chat_stream(self, messages: list[dict], tools: list[dict] = None):
        """
        流式调用 OpenAI 兼容 API（SSE），逐 token 产出。

        对标 ChatGPT 的逐字输出，前端可直接 SSE 转发。
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
            "stream": True,
        }

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # token 预算检查
        estimated = sum(len(str(m.get("content", ""))) for m in messages) // 2
        if not self._check_token_budget(estimated + 4096):
            yield ChatResponse(
                text="⚠️ API token 配额已用尽。\n\n"
                     f"本会话已使用 {self.total_tokens_used} tokens，"
                     f"达到上限 {self.max_total_tokens}。\n"
                     "请重置对话（/reset）或提高 max_total_tokens 配置。"
            )
            return

        try:
            resp = requests.post(
                url, headers=headers, json=body, timeout=self.timeout, stream=True
            )
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            yield ChatResponse(
                text=f"无法连接到 {self.base_url}，请检查网络或 base_url 配置。"
            )
            return
        except requests.exceptions.Timeout:
            yield ChatResponse(
                text=f"⏱️ 请求超时（{self.timeout}s）。请检查网络或增大 timeout 配置。"
            )
            return
        except requests.exceptions.HTTPError as e:
            yield self._handle_http_error(e)
            return
        except Exception as e:
            yield ChatResponse(text=f"请求异常：{e}")
            return

        # 解析 SSE 流
        buffer = ""
        collected_text = ""
        collected_tool_calls = {}
        finish_reason = "stop"
        usage = {}

        for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
            if not chunk:
                continue
            buffer += chunk
            lines = buffer.split("\n")
            # 除最后一行外都是完整行
            buffer = lines[-1]
            for line in lines[:-1]:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # 提取 token 用量
                    if data.get("usage"):
                        usage = data["usage"]

                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {}) or {}

                    # 文本 token
                    content = delta.get("content")
                    if content:
                        collected_text += content
                        yield ChatResponse(text=content, finish_reason="streaming")

                    # tool_calls token（逐块累积）
                    tc_delta = delta.get("tool_calls")
                    if tc_delta:
                        for tc in tc_delta:
                            idx = tc.get("index", 0)
                            if idx not in collected_tool_calls:
                                collected_tool_calls[idx] = {
                                    "id": tc.get("id", ""),
                                    "function": {"name": "", "arguments": ""},
                                }
                            func = tc.get("function", {})
                            if func.get("name"):
                                collected_tool_calls[idx]["function"]["name"] += func["name"]
                            if func.get("arguments"):
                                collected_tool_calls[idx]["function"]["arguments"] += func["arguments"]

                    # finish_reason
                    fr = choices[0].get("finish_reason")
                    if fr:
                        finish_reason = fr

        # 更新 token 用量
        if usage:
            self._last_usage = usage
            self.total_tokens_used += usage.get("total_tokens", 0)

        # 纯文本里夹带 function-call JSON 的兜底（后端不支持原生 tool_calls 时）
        if not collected_tool_calls and collected_text.strip():
            parsed = self._parse_json_toolcalls(collected_text)
            if parsed:
                yield ChatResponse(tool_calls=parsed, finish_reason="tool_calls")
                return

        # 如果有 tool_calls，最终产出完整结果
        if collected_tool_calls:
            tool_calls = []
            for idx in sorted(collected_tool_calls.keys()):
                tc = collected_tool_calls[idx]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "name": tc["function"]["name"],
                    "arguments": args,
                })
            yield ChatResponse(
                tool_calls=tool_calls,
                finish_reason="tool_calls",
            )

    def _handle_http_error(self, e: requests.exceptions.HTTPError) -> ChatResponse:
        """统一处理 HTTP 错误"""
        status = e.response.status_code if e.response is not None else "?"
        detail = ""
        if e.response is not None:
            try:
                detail = e.response.json().get("error", {}).get("message", "")
            except Exception:
                detail = e.response.text[:200]

        if status in self._ERROR_TIPS:
            title, tip = self._ERROR_TIPS[status]
            return ChatResponse(
                text=f"❌ {title}（HTTP {status}）\n\n{tip}\n\n详细信息：{detail}"
            )

        if "auth" in detail.lower() or "key" in detail.lower() or "token" in detail.lower():
            return ChatResponse(
                text=f"❌ 认证失败（HTTP {status}）\n\n"
                     "API key 无效或格式不正确。请检查 config.yaml 中的配置。\n\n"
                     f"详细信息：{detail}"
            )

        if any(kw in detail.lower() for kw in ("quota", "insufficient", "exhausted", "limit", "额度", "余额", "用量")):
            return ChatResponse(
                text=f"❌ API 额度耗尽\n\n"
                     f"API 返回：{detail}\n\n"
                     "建议：\n"
                     "  1. 检查账户余额是否充足\n"
                     "  2. token 配额是否已用完\n"
                     "  3. 重置对话后使用更小的模型"
            )

        return ChatResponse(
            text=f"❌ API 请求失败（HTTP {status}）\n\n{detail}\n\n"
                 "请检查网络连接和 API 配置。"
        )


def create_llm_client(config: dict) -> LLMClient:
    """
    工厂函数：根据配置创建 LLM 客户端

    用法：
        cfg = load_config()
        client = create_llm_client(cfg)
    """
    llm_cfg = config.get("llm", {})
    provider = llm_cfg.get("provider", "deterministic")

    if provider == "deterministic":
        return DeterministicBrain()

    if provider == "openai_compatible":
        base_url = llm_cfg.get("base_url", "")
        model = llm_cfg.get("model", "")
        # 百炼（DashScope）没填 model 时默认 qwen-plus，避免拿默认 gpt-4o-mini 去请求 403
        if not model and "dashscope" in base_url:
            model = "qwen-plus"
        return OpenAICompatibleClient(
            base_url=base_url,
            api_key=llm_cfg.get("api_key", ""),
            model=model,
            supports_vision=llm_cfg.get("supports_vision", False),
            timeout=llm_cfg.get("timeout", 60),
            max_total_tokens=llm_cfg.get("max_total_tokens", 0),
        )

    raise ValueError(f"不支持的 LLM provider: {provider}，可选: deterministic, openai_compatible")


# ============================================================
# 模型管理：列出可用模型 + 切换前连通性探测
# （供 Web 界面的模型下拉框使用）
# ============================================================

# 出现在 id 里就说明不是"对话/chat"模型的下划线标记（embedding/文生图/重排等）
_NON_CHAT_MARKERS = (
    "embedding", "rerank", "wanx", "image", "controlnet", "video",
    "face", "tts", "asr", "audio", "speech", "batch", "vector",
    "tokenizer", "dashscope.aigc", "pt_update", "qwen_mt", "translate",
)

# 拉不到模型列表时的兜底候选（切换时仍会逐次实测连通，不通不给用）
FALLBACK_CANDIDATES = [
    "qwen3.5-plus", "qwen-plus", "qwen-max", "qwen-turbo",
    "qwen2.5-72b-instruct", "qwen3-coder-plus", "qwen-vl-max",
]


def _is_chat_model(model_id: str) -> bool:
    """粗略判断某模型 id 是否可用于对话（仅用于筛选下拉列表，连通以 probe 实测为准）"""
    m = model_id.lower()
    return not any(marker in m for marker in _NON_CHAT_MARKERS)


def _filter_chat_models(model_ids: list) -> list:
    """去空、去重、过滤非对话模型，再排序"""
    return sorted({m for m in model_ids if m and _is_chat_model(m)})


def list_available_models(cfg: dict) -> dict:
    """
    通过 OpenAI 兼容的 GET /models 拉取当前 API 可用模型。

    返回 {"models": [...], "error": None|str}。失败时 models 为空数组、error 说明原因。
    """
    llm = cfg.get("llm", {})
    base_url = (llm.get("base_url") or "").rstrip("/")
    api_key = llm.get("api_key") or ""
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    if not base_url or not api_key:
        return {"models": [], "error": "未配置 base_url / api_key，无法获取模型列表"}

    url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        ids = [item.get("id", "") for item in data.get("data", [])]
        return {"models": _filter_chat_models(ids), "error": None}
    except requests.exceptions.RequestException as e:
        return {"models": [], "error": f"网络/接口错误：{e}"}
    except Exception as e:
        return {"models": [], "error": f"解析模型列表失败：{e}"}


def probe_model(cfg: dict, model: str) -> dict:
    """
    用指定模型发一条最小 chat 请求，实测是否真的连通可用。

    返回 {"ok": bool, "message": str}。message 面向用户，含失败原因。
    """
    llm = cfg.get("llm", {})
    base_url = (llm.get("base_url") or "").rstrip("/")
    api_key = llm.get("api_key") or ""
    if not base_url.endswith("/v1"):
        base_url += "/v1"
    if not base_url or not api_key:
        return {"ok": False, "message": "未配置 base_url / api_key"}
    if not model:
        return {"ok": False, "message": "未指定要切换的模型"}

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "连通性测试，请只回复：OK"}],
        "max_tokens": 16,
        "temperature": 0,
    }
    timeout = min(int(llm.get("timeout", 60) or 60), 30)

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.exceptions.ConnectionError:
        return {"ok": False, "message": f"无法连接到 {base_url}，请检查网络或 base_url"}
    except requests.exceptions.Timeout:
        return {"ok": False, "message": f"请求超时（>{timeout}s），网络慢或模型负载高"}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "message": f"请求异常：{e}"}

    if resp.status_code == 200:
        try:
            data = resp.json()
            if data.get("choices"):
                return {"ok": True, "message": "连接正常"}
            return {"ok": False, "message": "服务端返回了空内容"}
        except Exception:
            return {"ok": False, "message": "服务端返回了无法解析的内容"}

    # 非 200：给出贴近真实原因的提示
    tip = OpenAICompatibleClient._ERROR_TIPS.get(
        resp.status_code,
        ("请求失败", ""),
    )
    detail = ""
    try:
        detail = resp.json().get("error", {}).get("message", "")
    except Exception:
        detail = resp.text[:200]
    reason = tip[0]
    if detail:
        reason = f"{reason}：{detail[:300]}"
    return {"ok": False, "message": f"HTTP {resp.status_code} {reason}"}