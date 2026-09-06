"""
agent.py
第4组：Agent 编排层（ReAct 循环）

核心职责：
  1. 接收用户自然语言指令
  2. 通过 LLM 决定调用哪些工具
  3. 执行工具并返回结果给 LLM
  4. 循环直到 LLM 给出最终回复

设计参考：
  - ReAct（Reasoning + Acting）模式
  - OpenAI Function Calling / 工具调用格式
  - Microsoft UFO² 的 HostAgent 编排思路
  - ChatGPT 多会话管理 + 流式输出
"""

import io
import json
import os
import re
import base64
import shutil
import tempfile
import time
from typing import Callable, Optional, Generator

from core.agent_config import load_config
from core.conversation import Conversation, SessionManager
from core.llm_client import LLMClient, DeterministicBrain, OpenAICompatibleClient, create_llm_client, ChatResponse
from core.os_service_api import OSServiceAPI
from core.safety import get_meta, preview, requires_confirmation, ConfirmationDenied


# GUI 类工具列表（执行后建议截图回传 LLM）
_GUI_TOOLS = {
    "click_at", "send_hotkey", "send_text",
    "browser_launch", "browser_close", "browser_navigate",
    "browser_click", "browser_type",
    "activate_window",
}

# 注入给真实 LLM 的 system prompt（仅 openai_compatible 模式生效，离线脑不注入以免干扰关键词匹配）
SYSTEM_PROMPT = (
    "你是 miniyu，一个运行在用户本机的桌面 AI 助手。用户会用自然语言请你完成文件管理、"
    "系统操作、应用控制等任务。\n"
    "工具使用守则：\n"
    "1. 只在需要实际操作时调用已提供的工具，工具名和参数必须来自给定列表，不得编造工具名、路径或参数；\n"
    "2. 参数里的路径尽量使用用户给出的绝对路径，不明确时先向用户确认；\n"
    "3. 删除文件、向外发送消息等破坏性或外发操作，如非用户明确要求，先说明风险再执行；\n"
    "4. 每次工具调用后都会收到真实结果，请据此决定下一步；\n"
    "5. 任务完成时，用中文自然语言把关键结果总结给用户，不要只罗列工具调用；\n"
    "6. 往 QQ 等即时通讯软件里『搜索某个会话/群并发消息』（或需要在应用内进入某目标会话再操作）时，"
    "必须直接调用 app_send_message 这一个组合技能（它会自己搜索、用屏幕 OCR 核对会话标题、确认无误才发送），"
    "绝不拆成『激活窗口/点击/输入文字/回车』等零散步骤——那样可能发到错误的会话。\n"
    "7. 当你对当前执行状态不确定（工具结果含糊/要看界面再决定下一步/上一步可能出错想看清原因）时，"
    "先调用 screen_inspect 截一张当前屏幕来看，再决定下一步；不要因为界面情况不明就回复『做不到/太复杂/无法继续』。\n"
    "8. 发邮件必须调用 send_email 这一个组合技能（它会 SMTP 发信并自动 IMAP 回读核验已发送/到达），"
    "收件人与主题要跟用户说的完全一致，正文按用户要求写；发信账号与授权码在 config.yaml 的 email 段配好。"
)


# Agent 视觉能力函数（"截图理解"）：让模型在不确定 / 复杂 / 出错时主动看一眼屏幕。
# 它不属于 SkillLibrary（不改变 57 工具 / 25 技能计数），而是 Agent 自带的可调函数：
#   视觉模型 → 截图以原图内联给模型看；无视觉模型 → 调项目内视觉桥（config.yaml 的
#   vision_bridge 段，独立视觉 API）把图转成文字描述。
_AGENT_VISION_TOOL = {
    "type": "function",
    "function": {
        "name": "screen_inspect",
        "description": (
            "截取当前屏幕并理解界面状态（只读）。当你对上一步操作结果拿不准、需要确认当前界面长什么样、"
            "进入更深的界面/选项前要确认位置、或上一步执行报错需要看清原因时调用它。"
            "如果你是视觉模型会直接看到截图；否则系统会用 OCR/视觉桥把屏幕关键文字转成描述返回给你。"
            "截图会存为过程产物（可随时让用户说『清理截图』删除），不影响用户文件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": (
                        "你想从屏幕确认的问题，例如：现在界面上有哪些选项？聊天标题是什么？"
                        "上一步为什么可能失败？留空则描述整体界面。"
                    ),
                },
            },
            "required": [],
        },
    },
}

# 用户触发"清理过程产物/截图"的自然语言关键词（命中即清空当前会话产物目录）
_ARTIFACT_CLEAN_PHRASES = (
    "清理截图", "清除截图", "清空截图",
    "清理产物", "清除产物", "清空产物", "清理产出",
    "删除过程产物", "清理过程产物",
)


class Agent:
    """
    miniyu — Agent 编排层

    miniyu 是你的桌面 AI 助手，通过自然语言理解你的意图，
    调用系统工具帮你完成文件管理、系统操作、应用控制等任务。

    支持多会话管理（对标 ChatGPT 的会话切换）和流式输出。

    用法：
        agent = Agent()
        result = agent.run("帮我整理桌面")
        print(result)

        # 多会话管理
        agent.create_session("工作")
        agent.run("帮我整理桌面")

        # 流式输出
        for chunk in agent.run_stream("磁盘空间"):
            print(chunk.text, end="")
    """

    name = "miniyu"

    def __init__(
        self,
        config: dict = None,
        api: OSServiceAPI = None,
        llm_client: LLMClient = None,
        confirm_handler: Callable = None,
        system_prompt: str = None,
    ):
        """
        初始化 miniyu。

        参数：
            config:          配置字典（默认从 load_config 读取）
            api:             OSServiceAPI 实例（默认新建）
            llm_client:      LLM 客户端（默认根据 config 创建）
            confirm_handler: 高危操作确认回调（接收 preview dict，返回 True=放行 / False=拒绝）
                             None 表示自动放行
            system_prompt:   注入真实 LLM 的 system prompt（默认内置 SYSTEM_PROMPT；
                             仅 openai_compatible 模式生效）
        """
        self.config = config or load_config()
        self.api = api or OSServiceAPI()
        self.llm = llm_client or create_llm_client(self.config)
        self.confirm_handler = confirm_handler
        self.system_prompt = system_prompt or SYSTEM_PROMPT

        agent_cfg = self.config.get("agent", {})
        memory_cfg = self.config.get("memory", {})

        # 多会话管理器
        storage_dir = memory_cfg.get("storage_dir", "conversations")
        self.sessions = SessionManager(storage_dir=storage_dir)
        self.sessions.load_all()
        self.sessions.ensure_current()

        # 过程产物根目录：截图等 Agent/技能产生的工件独立存放（不混进对话记录与项目代码），
        # 便于同一任务内被下一个技能/工具复用，也便于按会话一句话清理。
        # 顺序：环境变量 AGENT_ARTIFACTS_DIR > config.memory.artifacts_dir > 系统临时目录。
        env_art = os.environ.get("AGENT_ARTIFACTS_DIR")
        if env_art:
            self.artifacts_root = env_art
        else:
            self.artifacts_root = memory_cfg.get(
                "artifacts_dir",
                os.path.join(tempfile.gettempdir(), "miniyu_artifacts"),
            )
        # 本轮（本次 run / run_stream）产生的产物清单 → 结束时提醒用户可清理
        self._turn_artifacts = []

        self.max_steps = agent_cfg.get("max_steps", 15)
        self.confirm_high_risk = agent_cfg.get("confirm_high_risk", True)
        self.window_size = agent_cfg.get("history_window", 20)

        # 记忆压缩（对标 AutoGPT 的上下文窗口管理）
        self.auto_summarize = memory_cfg.get("auto_summarize", False)
        self.summarize_threshold = memory_cfg.get("summarize_threshold", 0.8)

        # 规划层（对标 AutoGPT 的任务分解）
        self.enable_planning = agent_cfg.get("enable_planning", False)

        # 人类中断回调（对标 LangGraph 的 human-in-the-loop）
        self.interrupt_handler = None  # set_interrupt_handler(handler)

    # ============================================================
    # 兼容属性：self.conversation → 当前会话
    # ============================================================

    @property
    def conversation(self) -> Conversation:
        """当前会话（兼容旧代码）"""
        return self.sessions.ensure_current()

    # ============================================================
    # 会话管理（委托 SessionManager）
    # ============================================================

    def create_session(self, title: str = "") -> str:
        """创建新会话，对标 ChatGPT 的 New Chat"""
        return self.sessions.create(title=title)

    def switch_session(self, session_id: str) -> bool:
        """切换到指定会话"""
        return self.sessions.switch(session_id)

    def delete_session(self, session_id: str) -> bool:
        """删除会话"""
        return self.sessions.delete(session_id)

    def rename_session(self, session_id: str, title: str) -> bool:
        """重命名会话"""
        return self.sessions.rename(session_id, title)

    def list_sessions(self) -> list[dict]:
        """列出所有会话摘要"""
        return self.sessions.list()

    @property
    def current_session_id(self) -> Optional[str]:
        return self.sessions.current_id

    # ============================================================
    # 人类中断（对标 LangGraph 的 human-in-the-loop）
    # ============================================================

    def set_interrupt_handler(self, handler):
        """
        设置人类中断回调。

        当 Agent 需要用户确认或输入时调用此回调。
        对标 LangGraph 的 human-in-the-loop 模式。

        handler 签名：handler(interrupt_type: str, context: dict) -> dict
        返回 dict 包含用户的决策。
        """
        self.interrupt_handler = handler

    def _interrupt(self, interrupt_type: str, context: dict) -> dict:
        """触发中断，回调用户"""
        if self.interrupt_handler:
            return self.interrupt_handler(interrupt_type, context)
        return {"confirmed": True, "input": ""}

    # ============================================================
    # 规划层（对标 AutoGPT 的任务分解）
    # ============================================================

    def _plan(self, user_input: str) -> list[dict]:
        """
        将复杂任务分解为子任务列表。

        仅当 enable_planning=True 且为非离线模式时使用。
        对标 AutoGPT 的 planning + execution 分离模式。

        返回子任务列表：[{"step": "1", "action": "整理文件", "tool": "sort_directory", ...}]
        """
        if not self.enable_planning or isinstance(self.llm, DeterministicBrain):
            return [{"step": "1", "action": user_input}]

        messages = [
            {"role": "system", "content": "你是一个任务规划助手。将用户的复杂指令分解为1-5个具体步骤，"
                                          "返回 JSON 数组，每项包含 step/action/tool 字段。"},
            {"role": "user", "content": f"分解任务：{user_input}"},
        ]
        try:
            resp = self.llm.chat(messages)
            if resp.text:
                import json
                text = resp.text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
                if text.startswith("["):
                    steps = json.loads(text)
                    if isinstance(steps, list):
                        return steps
        except Exception:
            pass
        return [{"step": "1", "action": user_input}]

    def _compress_memory(self):
        """
        压缩对话记忆（当上下文接近上限时）。

        对标 AutoGPT 的内存压缩策略：
        当消息数超过窗口阈值时，用 LLM 总结早期对话。
        """
        if not self.auto_summarize or isinstance(self.llm, DeterministicBrain):
            return

        conv = self.conversation
        if len(conv.messages) < self.window_size * 2:
            return

        def summary_fn(old_messages):
            resp = self.llm.chat([
                {"role": "system", "content": "请用2-3句话总结以下对话的核心内容。"},
            ] + old_messages)
            return resp.text if resp.text else conv._simple_summary(old_messages)

        # 调用 get_summarized_window 触发压缩，同时设置 summary 字段
        conv.summary = summary_fn(conv.messages[:-self.window_size])

    # ============================================================
    # 主入口：非流式
    # ============================================================

    def run(self, user_input: str) -> str:
        """
        主入口：接收用户输入 → 执行 ReAct 循环 → 返回最终回复

        返回自然语言格式的回复。
        """
        if not user_input or not user_input.strip():
            return "请输入指令。"

        self._turn_artifacts = []
        self.conversation.add_user(user_input)

        # 用户说"清理截图/清理产物"等 → 直接清当前会话过程产物，不经过 LLM
        clean_msg = self._try_cleanup_command(user_input)
        if clean_msg is not None:
            self.sessions.save()
            return clean_msg

        # 规划层：分解复杂任务为子步骤（对标 AutoGPT）
        self.current_plan = self._plan(user_input)
        self.current_plan_index = 0
        plan_context = ""
        if len(self.current_plan) > 1:
            step_summary = " → ".join(s.get("action", s.get("step", "")) for s in self.current_plan[:3])
            plan_context = f"[规划: {step_summary}]"
            self.conversation.add_assistant(content=plan_context)

        # 获取工具描述（MCP 格式）
        tools = self.api.list_tools_mcp()

        # 获取工具描述（OpenAI 格式，用于真实 LLM）
        # = 57 个底层工具 + 白名单组合技能（app_send_message、send_email 等）+ 视觉能力函数 screen_inspect
        openai_tools = self._agent_openai_tools()

        for step in range(1, self.max_steps + 1):
            # ---- 1. 调用 LLM ----
            messages = self._request_messages()
            response = self.llm.chat(
                messages,
                tools=openai_tools if self.llm.provider == "openai_compatible" else None,
            )

            if not response:
                return "Agent 内部错误：LLM 无响应。"

            # ---- 2. 处理 LLM 响应 ----
            if response.finish_reason == "tool_calls" and response.tool_calls:
                self.conversation.add_assistant(
                    tool_calls=[
                        {"name": tc["name"], "arguments": tc["arguments"]}
                        for tc in response.tool_calls
                    ]
                )

                tool_call_ids = self.conversation.last_tool_call_ids()
                for i, tc in enumerate(response.tool_calls):
                    name = tc["name"]
                    args = tc["arguments"]
                    call_id = tool_call_ids[i] if i < len(tool_call_ids) else ""

                    # 执行工具（含安全确认）
                    result = self._execute_one(name, args)

                    # 用户拒绝高危操作 → 直接返回
                    if not result.get("success") and "拒绝" in result.get("error", ""):
                        return self._with_reminder(f"操作已取消：{result['error']}")

                    self.conversation.add_tool_result(
                        tool_call_id=call_id,
                        name=name,
                        content=result,
                    )

                    # screen_inspect 原生视觉分支：tool 结果之后把截图补成观测消息（顺序满足 API 配对要求）
                    self._attach_inspection_observation(result)

                    # GUI 操作 + 视觉模型 → 截图回传（成功/失败都截，失败用于诊断）
                    if name in _GUI_TOOLS and self.llm.supports_vision:
                        if result.get("success"):
                            self._capture_and_add_image(
                                note=f"已执行 {name}，当前屏幕如下，请结合它判断下一步。"
                            )
                        else:
                            self._capture_and_add_image(
                                note=f"执行 {name} 可能未成功，请结合截图看清原因再决定。"
                            )

                # 关键修复：离线脑执行完工具后直接返回结果，不再循环调 LLM
                if isinstance(self.llm, DeterministicBrain):
                    summary = self._format_deterministic_result(response.tool_calls, result)
                    self.sessions.save()
                    return self._with_reminder(summary)

            elif response.text:
                # 纯文本回复 → 最终回复
                self.conversation.add_assistant(content=response.text)
                self._compress_memory()  # 压缩记忆（对标 AutoGPT）
                self.sessions.save()
                return self._with_reminder(response.text)

            else:
                return "Agent 无法理解 LLM 的响应，请重试。"

        # 超出最大步数
        summary = f"任务未能在 {self.max_steps} 步内完成，已自动终止。"
        self.conversation.add_assistant(content=summary)
        self._compress_memory()
        self.sessions.save()
        return self._with_reminder(summary)

    # ============================================================
    # 流式入口
    # ============================================================

    def run_stream(self, user_input: str):
        """
        流式执行：逐 token 产出回复。

        对标 ChatGPT 的逐字输出效果。
        前端可配合 SSE 直接转发。

        用法：
            for chunk in agent.run_stream("磁盘空间"):
                if chunk.finish_reason == "streaming":
                    print(chunk.text, end="")
                elif chunk.finish_reason == "tool_calls":
                    print(f"[调用工具: {chunk.tool_calls}]")
                elif chunk.finish_reason == "stop":
                    print(f"[完成: {chunk.text}]")

        产出：
            ChatResponse 对象，finish_reason 为 "streaming" 表示中间 token，
            "tool_calls" 表示工具调用，"stop" 表示最终回复。
        """
        if not user_input or not user_input.strip():
            yield ChatResponse(text="请输入指令。", finish_reason="stop")
            return

        self._turn_artifacts = []
        self.conversation.add_user(user_input)

        # 用户说"清理截图/清理产物"等 → 直接清当前会话过程产物，不经过 LLM
        clean_msg = self._try_cleanup_command(user_input)
        if clean_msg is not None:
            yield ChatResponse(text=clean_msg, finish_reason="stop")
            self.sessions.save()
            return

        openai_tools = self._agent_openai_tools()

        for step in range(1, self.max_steps + 1):
            messages = self._request_messages()
            collected_text = ""

            for chunk in self.llm.chat_stream(
                messages,
                tools=openai_tools if self.llm.provider == "openai_compatible" else None,
            ):
                if chunk.finish_reason == "streaming":
                    collected_text += chunk.text
                    yield chunk
                elif chunk.finish_reason == "tool_calls":
                    yield chunk
                    # 处理工具调用
                    self.conversation.add_assistant(
                        tool_calls=[
                            {"name": tc["name"], "arguments": tc["arguments"]}
                            for tc in chunk.tool_calls
                        ]
                    )

                    tool_call_ids = self.conversation.last_tool_call_ids()
                    for i, tc in enumerate(chunk.tool_calls):
                        name = tc["name"]
                        args = tc["arguments"]
                        call_id = tool_call_ids[i] if i < len(tool_call_ids) else ""
                        result = self._execute_one(name, args)

                        if not result.get("success") and "拒绝" in result.get("error", ""):
                            yield ChatResponse(
                                text=self._with_reminder(f"操作已取消：{result['error']}"),
                                finish_reason="stop",
                            )
                            self.sessions.save()
                            return

                        self.conversation.add_tool_result(
                            tool_call_id=call_id,
                            name=name,
                            content=result,
                        )

                        # screen_inspect 原生视觉分支：tool 结果之后把截图补成观测消息
                        self._attach_inspection_observation(result)

                        # GUI 操作 + 视觉模型 → 截图回传（成功/失败都截，失败用于诊断）
                        if name in _GUI_TOOLS and self.llm.supports_vision:
                            if result.get("success"):
                                self._capture_and_add_image(
                                    note=f"已执行 {name}，当前屏幕如下，请结合它判断下一步。"
                                )
                            else:
                                self._capture_and_add_image(
                                    note=f"执行 {name} 可能未成功，请结合截图看清原因再决定。"
                                )

                    # 离线脑执行完工具后直接返回
                    if isinstance(self.llm, DeterministicBrain):
                        summary = self._format_deterministic_result(chunk.tool_calls, result)
                        yield ChatResponse(text=self._with_reminder(summary), finish_reason="stop")
                        self.sessions.save()
                        return

                    # 继续下一轮 ReAct 循环
                    break
                else:
                    # finish_reason == "stop"
                    if chunk.text:
                        self.conversation.add_assistant(content=chunk.text)
                    reminder = self._artifact_reminder()
                    if reminder:
                        yield ChatResponse(text=reminder, finish_reason="streaming")
                    yield chunk
                    self.sessions.save()
                    return
            else:
                # 内层流正常结束（没有 break）→ 说明是纯文本回复且已收集完
                # 补一个终止 chunk，避免外层又开新一轮重调 LLM 直到 max_steps。
                if collected_text:
                    self.conversation.add_assistant(content=collected_text)
                    reminder = self._artifact_reminder()
                    if reminder:
                        yield ChatResponse(text=reminder, finish_reason="streaming")
                    yield ChatResponse(finish_reason="stop")
                    self.sessions.save()
                    return
                continue
            # 如果 break 了（tool_calls），继续下一轮
            continue

        # 超出最大步数
        summary = f"任务未能在 {self.max_steps} 步内完成，已自动终止。"
        self.conversation.add_assistant(content=summary)
        yield ChatResponse(text=self._with_reminder(summary), finish_reason="stop")
        self.sessions.save()

    # ============================================================
    # 内部方法
    # ============================================================

    def _request_messages(self) -> list:
        """
        构造发送给 LLM 的消息列表。

        真实 LLM 请求在对话历史前注入 system prompt（提升工具调用可靠性）；
        离线脑（DeterministicBrain）靠扫描全部 content 做关键词匹配，
        不注入 system，避免干扰意图识别。
        """
        messages = self.conversation.get_window()
        if self.llm.provider == "openai_compatible":
            return [{"role": "system", "content": self.system_prompt}] + messages
        return messages

    def _execute_one(self, name: str, args: dict) -> dict:
        """
        执行单个工具/技能调用（含安全确认）。

        白名单组合技能（如 app_send_message）走技能库（_execute_skill），
        其余走底层工具注册表（_execute_tool）；screen_inspect 是 Agent 自带的
        视觉能力函数（截图理解），单独分发。
        """
        if name == "screen_inspect":
            return self._execute_screen_inspect(args)
        if self.api.is_agent_skill(name):
            return self._execute_skill(name, args)
        return self._execute_tool(name, args)

    def _execute_tool(self, name: str, args: dict) -> dict:
        """执行单个底层工具（含安全确认）"""
        meta = get_meta("tool", name)

        # 高危操作 + 确认模式开启 → 走确认门
        if self.confirm_high_risk and requires_confirmation(meta["risk"]):
            pv = preview("tool", name, args, meta)
            if self.confirm_handler:
                try:
                    self.confirm_handler(pv)
                except ConfirmationDenied:
                    return {
                        "success": False,
                        "tool": name,
                        "error": f"用户拒绝了高危操作：{name}",
                    }
                except Exception:
                    return {
                        "success": False,
                        "tool": name,
                        "error": f"用户拒绝了高危操作：{name}",
                    }

        # 执行工具
        try:
            result = self.api.execute_tool(name, args)
            return result if isinstance(result, dict) else {
                "success": True,
                "tool": name,
                "result": result,
            }
        except Exception as e:
            return {
                "success": False,
                "tool": name,
                "error": str(e),
            }

    def _execute_skill(self, name: str, args: dict) -> dict:
        """执行 Agent 白名单组合技能（HIGH 风险走确认门）"""
        # 发送类技能默认开 OCR 门：模型没给 verify_ocr 键时补 True，让技能内部用屏幕
        # OCR 核对是否进入目标会话，防止发到错误会话。显式传了 verify 闭包或显式
        # verify_ocr=False 时不覆盖（尊重调用方）。
        if name == "app_send_message" and "verify" not in args and "verify_ocr" not in args:
            args["verify_ocr"] = True

        meta = get_meta("skill", name)

        # 高危操作 + 确认模式开启 → 走确认门
        if self.confirm_high_risk and requires_confirmation(meta["risk"]):
            pv = preview("skill", name, args, meta)
            if self.confirm_handler:
                try:
                    self.confirm_handler(pv)
                except ConfirmationDenied:
                    return {
                        "success": False,
                        "skill": name,
                        "error": f"用户拒绝了高危操作：{name}",
                    }
                except Exception:
                    return {
                        "success": False,
                        "skill": name,
                        "error": f"用户拒绝了高危操作：{name}",
                    }

        # 执行技能
        try:
            result = self.api.run_skill(name, args)
            return result if isinstance(result, dict) else {
                "success": True,
                "skill": name,
                "result": result,
            }
        except Exception as e:
            return {
                "success": False,
                "skill": name,
                "error": str(e),
            }

    # ============================================================
    # 过程产物（截图等）生命周期 + 截图理解（视觉通道）
    # ============================================================

    def _session_artifact_dir(self) -> str:
        """当前会话专属产物目录（不存在则创建）。截图等过程产物统一放这里。"""
        sid = self.current_session_id or "default"
        sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(sid)) or "default"
        d = os.path.join(self.artifacts_root, sid)
        os.makedirs(d, exist_ok=True)
        return d

    def _record_artifact(self, path: str):
        """登记一个本轮产物（用于结束提醒与清理统计）"""
        if path and path not in self._turn_artifacts:
            self._turn_artifacts.append(path)

    def _artifact_reminder(self) -> str:
        """本轮产生了过程产物 → 返回一句"可清理"提醒；否则空串"""
        if not self._turn_artifacts:
            return ""
        try:
            d = os.path.dirname(self._turn_artifacts[0])
        except Exception:
            d = self.artifacts_root
        return (
            f"\n\n📎 本轮为排查/操作生成了 {len(self._turn_artifacts)} 个过程产物"
            f"（含屏幕截图，可能带屏幕内容），存于 {d}。"
            f"如需删除，对我说『清理截图/清理产物』即可（/reset 也会自动清理）。"
        )

    def _with_reminder(self, text: str) -> str:
        """给最终回复附上产物提醒（仅在确实产生过产物时追加）"""
        r = self._artifact_reminder()
        return text + r if r else text

    def _agent_openai_tools(self) -> list:
        """模型可见函数全集 = 57 底层工具 + 白名单组合技能 + 视觉能力函数 screen_inspect"""
        return self.api.list_tools_openai() + self.api.list_skills_openai() + [dict(_AGENT_VISION_TOOL)]

    def _capture_and_add_image(self, note: str = ""):
        """
        截图 → 存入过程产物目录 → 把【真实 base64】以观测消息加入对话（供视觉模型查看）。

        修复背景：早期实现把 take_screenshot 返回的【文件路径字符串】当 base64 直接拼进
        data:image/... URL，模型拿到的是坏图；这里改为读文件后再 base64。
        拿不到可读图片（如 mock / 测试环境无真截图）时静默返回 None。
        """
        try:
            shots = os.path.join(self._session_artifact_dir(), "shots")
            os.makedirs(shots, exist_ok=True)
            path = os.path.join(shots, f"shot_{int(time.time() * 1000)}.png")
            scr = self.api.execute_tool("take_screenshot", {"output": path})
            img_path = scr.get("result") if scr.get("success") else None
            if not isinstance(img_path, str):
                img_path = (img_path or {}).get("path")
            if not img_path or not os.path.isfile(img_path):
                return None
            with open(img_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self.conversation.add_observation_image(note or "（当前界面截图）", b64)
            self._record_artifact(img_path)
            return img_path
        except Exception:
            return None

    def _run_vision_bridge(self, image_path: str, question: str) -> Optional[str]:
        """调项目内视觉桥（config.yaml 的 vision_bridge 段，独立于主 llm 的视觉 API）
        把截图转成文字描述；视觉桥没配/失败返回 None（调用方给用户明确指引）。"""
        from core import vision_bridge as _vb
        try:
            return _vb.describe_image(image_path, question)
        except _vb.VisionBridgeError:
            return None
        except Exception:
            return None

    def _execute_screen_inspect(self, args: dict) -> dict:
        """screen_inspect：截当前屏幕并让 Agent 看懂。

        主模型有视觉（llm.supports_vision=true） → 返回 mode=image + 截图路径，
        调用方在 tool 结果之后把它以真实 base64 观测消息补进对话，模型下一轮直接看原图；
        主模型无视觉 → 调项目内视觉桥（config.yaml vision_bridge 段的独立视觉 API）
        把图转成文字描述，直接作为结果返回。
        截图一律先落到当前会话的过程产物目录（可说『清理截图』删除）。
        """
        question = (args or {}).get("question") or (
            "请描述当前屏幕：界面状态、关键文字/报错、大致布局，帮助判断下一步。")
        try:
            shots = os.path.join(self._session_artifact_dir(), "shots")
            os.makedirs(shots, exist_ok=True)
            path = os.path.join(shots, f"inspect_{int(time.time() * 1000)}.png")
            scr = self.api.execute_tool("take_screenshot", {"output": path})
            img_path = scr.get("result") if scr.get("success") else None
            if not isinstance(img_path, str):
                img_path = (img_path or {}).get("path")
            if not img_path or not os.path.isfile(img_path):
                return {"success": False, "screen_inspect": True,
                        "error": "截屏失败：没有拿到可读的图片文件。"}
            self._record_artifact(img_path)

            if getattr(self.llm, "supports_vision", False):
                # 原生视觉模型：图片随后由调用方以观测消息补进对话
                return {
                    "success": True, "screen_inspect": True, "mode": "image",
                    "result": {
                        "screenshot_path": img_path,
                        "note": f"[screen_inspect] {question}",
                    },
                }

            # 无视觉主模型：走项目内视觉桥（vision_bridge 段）→ 文字描述
            desc = self._run_vision_bridge(img_path, question)
            if desc is None:
                return {
                    "success": False, "screen_inspect": True,
                    "error": "当前主模型不支持看图，且视觉桥没配好。请在 config.yaml 的 "
                            "vision_bridge 段填一个有视觉的 API 模型（base_url/api_key/model，"
                            "可和主对话 llm 用不同 key/厂商），或改用有视觉的主模型 "
                            "（llm.supports_vision: true）。",
                }
            return {
                "success": True, "screen_inspect": True, "mode": "text",
                "result": {"screenshot_path": img_path, "description": desc},
            }
        except Exception as e:
            return {"success": False, "screen_inspect": True, "error": f"截图理解失败：{e}"}

    def _attach_inspection_observation(self, result: dict):
        """screen_inspect 原生视觉分支：在 tool 结果之后，把截图以真实 base64 观测消息补进对话"""
        if not (result or {}).get("screen_inspect") or result.get("mode") != "image":
            return
        info = result.get("result") or {}
        p = info.get("screenshot_path")
        if not p or not os.path.isfile(p):
            return
        try:
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            self.conversation.add_observation_image(
                info.get("note") or "（当前界面截图）", b64)
        except Exception:
            pass

    def _cleanup_current_session_artifacts(self) -> int:
        """删除当前会话产物目录，返回删除的文件数；没有目录返回 0"""
        sid = self.current_session_id or "default"
        sid = re.sub(r"[^A-Za-z0-9_-]", "_", str(sid)) or "default"
        d = os.path.join(self.artifacts_root, sid)
        n = 0
        if os.path.isdir(d):
            for _root, _dirs, files in os.walk(d):
                n += len(files)
            shutil.rmtree(d, ignore_errors=True)
        return n

    def _try_cleanup_command(self, user_input: str) -> Optional[str]:
        """用户说『清理截图/清理产物』等 → 清空当前会话产物目录并返回提示；否则返回 None"""
        text = re.sub(r"[\s，。,.!！?？/、：:;；]", "", user_input or "")
        if not any(p in text for p in _ARTIFACT_CLEAN_PHRASES):
            return None
        n = self._cleanup_current_session_artifacts()
        if n:
            return f"已清理当前会话的 {n} 个过程产物（截图等）。目录已删除，聊天记录不受影响。"
        return "当前会话没有需要清理的过程产物。"

    def reset(self):
        """重置当前会话：清空聊天记录，并联动清空当前会话过程产物"""
        self.conversation.clear()
        self._cleanup_current_session_artifacts()
        self.sessions.save()

    @property
    def provider(self) -> str:
        return self.llm.provider

    @property
    def model_name(self) -> str:
        """获取当前使用的模型名称"""
        if isinstance(self.llm, DeterministicBrain):
            return "离线脑（规则匹配）"
        if hasattr(self.llm, "model") and self.llm.model:
            return self.llm.model
        return self.llm.provider

    # ============================================================
    # 格式化方法
    # ============================================================

    def _format_deterministic_result(self, tool_calls: list, last_result: dict) -> str:
        """将离线脑执行结果格式化为可读文本"""
        lines = []
        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            lines.append(f"▸ 执行: {name}")

        if not last_result.get("success"):
            return f"执行失败：{last_result.get('error', '未知错误')}"

        result_data = last_result.get("result", last_result)
        tool_name = tool_calls[-1]["name"] if tool_calls else ""

        if tool_name == "disk_usage":
            return self._format_disk_usage(result_data)
        elif tool_name == "list_processes":
            return self._format_processes(result_data)
        elif tool_name == "network_status":
            return self._format_network(result_data)
        elif tool_name == "find_large_files":
            return self._format_large_files(result_data)
        elif tool_name in ("list_directory", "search_files"):
            return self._format_files(result_data)
        elif tool_name == "list_env_vars":
            return self._format_env_vars(result_data)
        else:
            if isinstance(result_data, dict):
                return "\n".join(f"{k}: {v}" for k, v in result_data.items())
            if isinstance(result_data, list):
                items = "\n".join(str(item) for item in result_data[:20])
                if len(result_data) > 20:
                    items += f"\n... 还有 {len(result_data) - 20} 项"
                return items
            return str(result_data)

    @staticmethod
    def _format_disk_usage(data) -> str:
        """格式化磁盘使用信息"""
        if isinstance(data, dict):
            total = data.get("total", 0)
            used = data.get("used", 0)
            free = data.get("free", 0)
            percent = data.get("percent_used", 0)
            total_gb = total / (1024**3) if total else 0
            used_gb = used / (1024**3) if used else 0
            free_gb = free / (1024**3) if free else 0
            return (
                f"磁盘使用情况：\n"
                f"  总容量: {total_gb:.1f} GB\n"
                f"  已用: {used_gb:.1f} GB ({percent}%)\n"
                f"  剩余: {free_gb:.1f} GB"
            )
        return str(data)

    @staticmethod
    def _format_processes(data) -> str:
        """格式化进程列表"""
        if isinstance(data, list):
            lines = ["进程列表（按 CPU 占用排序）："]
            for i, p in enumerate(data[:15], 1):
                name = p.get("name", p.get("Name", "?"))
                pid = p.get("pid", p.get("PID", "?"))
                cpu = p.get("cpu_percent", p.get("CPU", 0))
                mem = p.get("memory_percent", p.get("Memory", 0))
                lines.append(f"  {i}. {name} (PID: {pid}) CPU: {cpu}% 内存: {mem}%")
            if len(data) > 15:
                lines.append(f"  ... 共 {len(data)} 个进程")
            return "\n".join(lines)
        return str(data)

    @staticmethod
    def _format_network(data) -> str:
        """格式化网络状态"""
        if isinstance(data, dict):
            lines = ["网络状态："]
            for k, v in data.items():
                lines.append(f"  {k}: {v}")
            return "\n".join(lines)
        return str(data)

    @staticmethod
    def _format_large_files(data) -> str:
        """格式化大文件列表"""
        if isinstance(data, list):
            lines = ["大文件列表："]
            for i, f in enumerate(data[:20], 1):
                path = f.get("path", f.get("Path", "?"))
                size = f.get("size_mb", f.get("Size", 0))
                lines.append(f"  {i}. {path} ({size} MB)")
            if len(data) > 20:
                lines.append(f"  ... 共 {len(data)} 个文件")
            return "\n".join(lines)
        return str(data)

    @staticmethod
    def _format_files(data) -> str:
        """格式化文件列表"""
        if isinstance(data, list):
            lines = [f"文件列表（共 {len(data)} 项）："]
            for i, f in enumerate(data[:30], 1):
                if isinstance(f, dict):
                    name = f.get("name", f.get("Name", "?"))
                    ftype = f.get("type", f.get("Type", ""))
                    lines.append(f"  {i}. [{ftype}] {name}")
                else:
                    lines.append(f"  {i}. {f}")
            if len(data) > 30:
                lines.append(f"  ... 还有 {len(data) - 30} 项")
            return "\n".join(lines)
        return str(data)

    @staticmethod
    def _format_env_vars(data) -> str:
        """格式化环境变量"""
        if isinstance(data, list):
            lines = ["环境变量："]
            for i, env in enumerate(data[:20], 1):
                key = env.get("key", env.get("Key", "?"))
                val = env.get("value", env.get("Value", ""))
                display = val[:60] + "..." if len(str(val)) > 60 else val
                lines.append(f"  {key}={display}")
            if len(data) > 20:
                lines.append(f"  ... 共 {len(data)} 个环境变量")
            return "\n".join(lines)
        return str(data)