#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
miniyu — Web 桌面客户端

你的桌面 AI 助手，通过浏览器访问。
支持离线脑和真实 LLM 双模式，高危操作弹出确认对话框。

用法：
    pip install flask
    python examples/miniyu_web.py

然后浏览器打开 http://localhost:5000

可移植性说明：
    - 所有路径使用相对路径，别人拿到项目直接运行
    - 配置文件 config.yaml 自带注释说明
    - 只需 pip install -r requirements.txt 安装依赖
    - 如用真实 LLM，配置环境变量或 config.yaml 中的 API key
    - 零配置启动即为离线模式（任何机器都能跑）
"""

import sys
import json
import queue
import threading
import uuid
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, request, jsonify, Response

from core.agent import Agent
from core.agent_config import load_config, set_config_model, set_config_authorization
from core.llm_client import list_available_models, probe_model, FALLBACK_CANDIDATES, FailoverClient
from core.safety import ConfirmationDenied
from core.safety import AUTHZ_LEVELS, AUTHZ_LABELS, resolve_authz_level

app = Flask(__name__)

# 全局状态
_agent = None
_agent_lock = threading.Lock()

# 任务状态管理
# {task_id: {"status": "pending"|"confirm"|"done"|"error",
#             "confirm_data": dict,
#             "result": str,
#             "confirm_event": threading.Event,
#             "confirm_result": bool,
#             "events": queue.Queue — SSE 事件流（reasoning/token/tool_call/confirm/done/error）}}
_tasks = {}
_tasks_lock = threading.Lock()


# ============================================================
# 确认处理器（线程安全，异步阻塞）
# ============================================================

def _web_confirm_handler(pv):
    """
    Web 确认处理器。

    在 Agent.run() 的线程中被调用。
    1. 存储确认数据到共享状态 + 推送 confirm 事件到 SSE 流
    2. 阻塞等待前端通过 /confirm 接口放行/拒绝
    """
    task_id = _get_current_task_id()
    if not task_id:
        raise ConfirmationDenied(pv.get("name", ""))

    event = threading.Event()
    confirm_data = {
        "tool": pv.get("name", ""),
        "description": pv.get("description", ""),
        "arguments": pv.get("params", {}),
        "risk": pv.get("risk", "high"),
    }
    with _tasks_lock:
        # 保留已有的 SSE 事件队列与停止事件（任务创建时生成），确认只更新其余字段
        old_task = _tasks.get(task_id, {})
        old_events = old_task.get("events")
        old_stop = old_task.get("stop_event")
        _tasks[task_id] = {
            "status": "confirm",
            "confirm_data": confirm_data,
            "confirm_event": event,
            "confirm_result": None,
            "result": None,
            "events": old_events,
            "stop_event": old_stop,
        }
        eq = old_events

    # 推送 confirm 事件到 SSE 流（前端弹确认框；事件队列不存在则忽略——轮询模式兜底）
    if eq is not None:
        eq.put({"type": "confirm", **confirm_data})

    # 等待前端确认（超时 120 秒）
    if not event.wait(timeout=120):
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = "确认超时，操作已取消。"
        raise ConfirmationDenied(pv.get("name", ""))

    with _tasks_lock:
        result = _tasks[task_id].get("confirm_result", False)

    if not result:
        raise ConfirmationDenied(pv.get("name", ""))

    return True


# 任务 ID 管理（线程局部变量）
_current_task = threading.local()

def _get_current_task_id():
    return getattr(_current_task, "task_id", None)

def _set_current_task_id(task_id):
    _current_task.task_id = task_id


# ============================================================
# 后台 Agent 执行线程
# ============================================================

def _ensure_agent():
    """惰性创建全局 Agent（线程安全）。/status 与后台任务共用，保证状态如实反映配置"""
    global _agent
    with _agent_lock:
        if _agent is None:
            _agent = Agent(
                config=load_config(),
                confirm_handler=_web_confirm_handler,
            )
    return _agent


def _run_agent_thread(task_id: str, message: str):
    """
    在后台线程中执行 Agent.run_stream()，把过程事件推入 SSE 队列。

    事件类型（前端据此渲染 DeepSeek 式思考展示）：
      reasoning — 思考 token 增量（深度思考模型）
      token     — 正文 token 增量
      tool_call — 模型决定调用某工具（含参数）
      confirm   — 高危操作待用户确认（_web_confirm_handler 推送）
      done/error— 终态，附完整回复文本
    """
    _set_current_task_id(task_id)

    def ev(obj):
        with _tasks_lock:
            eq = _tasks.get(task_id, {}).get("events")
        if eq is not None:
            eq.put(obj)

    def finish(status, result):
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["status"] = status
                _tasks[task_id]["result"] = result
        ev({"type": status, "result": result})

    try:
        _agent = _ensure_agent()
        final_text = ""
        got_stop = False

        # "停止生成"：/task/<id>/stop 置位 stop_event，agent 在流式 chunk
        # 间隙检测到后立即终止（保留已流出文本），用于打断死循环/超长输出
        with _tasks_lock:
            stop_event = _tasks.get(task_id, {}).get("stop_event")
        stop_check = stop_event.is_set if stop_event is not None else None

        for chunk in _agent.run_stream(message, stop_check=stop_check):
            if chunk.finish_reason == "reasoning":
                ev({"type": "reasoning", "delta": chunk.reasoning})
            elif chunk.finish_reason == "streaming":
                if chunk.text:
                    final_text += chunk.text
                    ev({"type": "token", "delta": chunk.text})
            elif chunk.finish_reason == "tool_calls":
                for tc in chunk.tool_calls:
                    ev({"type": "tool_call", "tool": tc.get("name", "?"),
                        "arguments": tc.get("arguments", {})})
            elif chunk.finish_reason == "stop":
                if chunk.text:
                    # stop chunk 携带完整最终回复：整体替换，避免与流式增量重复拼接
                    final_text = chunk.text
                got_stop = True
                finish("done", final_text)
                return

        # 流正常结束但没收到 stop chunk（防御）：以已累积文本收尾
        if not got_stop:
            finish("done", final_text or "（无输出）")

    except ConfirmationDenied:
        # 用户拒绝/超时：agent 已把"操作已取消"作为 stop chunk 吐出；
        # 若没收到（异常路径直接抛出），在此补一个终态
        with _tasks_lock:
            status = _tasks.get(task_id, {}).get("status")
        if status != "done":
            finish("done", "⛔ 操作已取消。")
    except Exception as e:
        finish("error", f"出错了: {e}")


# ============================================================
# HTML 页面（单文件，内置所有样式和脚本）
# ============================================================

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>miniyu — 桌面 AI 助手</title>
<style>
	  :root {
	    --bg: #1a1a2e;
	    --surface: #16213e;
	    --card: #0f3460;
	    --accent: #e94560;
	    --text: #eaeaea;
	    --text-secondary: #a0a0b0;
	    --border: #2a2a4a;
	    --success: #4ade80;
	    --warning: #fbbf24;
	    --danger: #ef4444;
	    --sidebar-width: 280px;
	  }
	  * { margin: 0; padding: 0; box-sizing: border-box; }
	  body {
	    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
	    background: var(--bg);
	    color: var(--text);
	    height: 100vh;
	    display: flex;
	  }
	  /* 侧边栏 */
	  .sidebar {
	    width: var(--sidebar-width);
	    background: var(--surface);
	    border-right: 1px solid var(--border);
	    display: flex;
	    flex-direction: column;
	    flex-shrink: 0;
	    overflow: hidden;
	  }
	  .sidebar-header {
	    padding: 16px;
	    border-bottom: 1px solid var(--border);
	    display: flex;
	    justify-content: space-between;
	    align-items: center;
	  }
	  .sidebar-header h3 {
	    font-size: 14px;
	    color: var(--text-secondary);
	    font-weight: 600;
	    text-transform: uppercase;
	    letter-spacing: 1px;
	  }
	  .btn-new-session {
	    background: var(--accent);
	    color: white;
	    border: none;
	    border-radius: 6px;
	    padding: 6px 14px;
	    font-size: 12px;
	    cursor: pointer;
	    font-weight: 600;
	  }
	  .btn-new-session:hover { opacity: 0.85; }
	  .session-list {
	    flex: 1;
	    overflow-y: auto;
	    padding: 8px;
	  }
	  .session-item {
	    padding: 10px 12px;
	    border-radius: 8px;
	    cursor: pointer;
	    margin-bottom: 4px;
	    transition: background 0.15s;
	    display: flex;
	    justify-content: space-between;
	    align-items: center;
	  }
	  .session-item:hover { background: rgba(255,255,255,0.05); }
	  .session-item.active {
	    background: var(--card);
	    border-left: 3px solid var(--accent);
	  }
	  .session-item-title {
	    font-size: 13px;
	    font-weight: 500;
	    white-space: nowrap;
	    overflow: hidden;
	    text-overflow: ellipsis;
	    flex: 1;
	  }
	  .session-item-meta {
	    font-size: 11px;
	    color: var(--text-secondary);
	    margin-left: 8px;
	    white-space: nowrap;
	  }
	  .session-item .delete-btn {
	    display: none;
	    background: none;
	    border: none;
	    color: var(--danger);
	    cursor: pointer;
	    font-size: 14px;
	    padding: 2px 6px;
	    border-radius: 4px;
	  }
	  .session-item:hover .delete-btn { display: block; }
	  .session-item .delete-btn:hover { background: rgba(239,68,68,0.15); }
	  /* 主区域 */
	  .main {
	    flex: 1;
	    display: flex;
	    flex-direction: column;
	    min-width: 0;
	  }
	  .header {
	    background: var(--surface);
	    border-bottom: 1px solid var(--border);
	    padding: 12px 24px;
	    display: flex;
	    align-items: center;
	    justify-content: space-between;
	    flex-shrink: 0;
	  }
	  .header-title { font-size: 20px; font-weight: 700; color: var(--accent); }
	  .header-title span { color: var(--text-secondary); font-weight: 400; font-size: 14px; }
	  .header-info {
	    font-size: 12px;
	    color: var(--text-secondary);
	    display: flex;
	    gap: 16px;
	    align-items: center;
	  }
	  .badge {
	    padding: 2px 8px;
	    border-radius: 4px;
	    font-size: 11px;
	    background: var(--card);
	  }
  .authz-select {
		    background: var(--card);
		    color: var(--text);
		    border: 1px solid var(--border);
		    border-radius: 4px;
		    padding: 2px 8px;
		    font-size: 12px;
		    outline: none;
		    cursor: pointer;
		    max-width: 118px;
		  }
		  .authz-select:disabled { opacity: 0.6; cursor: not-allowed; }
			  /* 联网搜索开关（DeepSeek 式总开关：管百炼服务端搜索 + 本地搜索工具） */
			  .websearch-toggle {
			    background: var(--card);
			    color: var(--text-secondary);
			    border: 1px solid var(--border);
			    border-radius: 4px;
			    padding: 3px 10px;
			    font-size: 12px;
			    cursor: pointer;
			    white-space: nowrap;
			    transition: all .15s;
			  }
			  .websearch-toggle.on {
			    background: var(--accent);
			    color: #fff;
			    border-color: var(--accent);
			  }
			  .websearch-toggle:disabled { opacity: 0.6; cursor: not-allowed; }
			  /* 可搜索模型下拉框（Combobox） */
		  .model-search-wrapper {
		    position: relative;
		    max-width: 280px;
		    min-width: 180px;
		  }
		  .model-combobox {
		    position: relative;
		  }
		  .model-search-input {
		    width: 100%;
		    background: var(--card);
		    color: var(--text);
		    border: 1px solid var(--border);
		    border-radius: 4px;
		    padding: 4px 24px 4px 8px;
		    font-size: 12px;
		    outline: none;
		    box-sizing: border-box;
		    cursor: pointer;
		  }
		  .model-search-input:focus { border-color: var(--accent); }
		  .model-search-input::placeholder { color: var(--text-secondary); font-size: 11px; }
		  .model-search-input:disabled { opacity: 0.6; cursor: not-allowed; }
		  .model-search-clear {
		    position: absolute;
		    right: 6px;
		    top: 50%;
		    transform: translateY(-50%);
		    cursor: pointer;
		    color: var(--text-secondary);
		    font-size: 14px;
		    display: none;
		    line-height: 1;
		    padding: 0 2px;
		    user-select: none;
		  }
		  .model-search-clear:hover { color: var(--text); }
		  .model-dropdown {
		    display: none;
		    position: absolute;
		    top: 100%;
		    left: 0;
		    right: 0;
		    max-height: 320px;
		    overflow-y: auto;
		    background: var(--surface);
		    border: 1px solid var(--border);
		    border-radius: 6px;
		    z-index: 1000;
		    margin-top: 4px;
		    box-shadow: 0 6px 20px rgba(0,0,0,0.4);
		  }
		  .model-dropdown.show { display: block; }
		  .model-dropdown-item {
		    padding: 7px 10px;
		    font-size: 12px;
		    cursor: pointer;
		    display: flex;
		    justify-content: space-between;
		    align-items: center;
		    transition: background 0.1s;
		  }
		  .model-dropdown-item:hover { background: rgba(255,255,255,0.08); }
		  .model-dropdown-item.active {
		    background: var(--card);
		    border-left: 3px solid var(--accent);
		  }
		  .model-dropdown-group-label {
		    padding: 5px 10px;
		    font-size: 11px;
		    color: var(--text-secondary);
		    background: rgba(255,255,255,0.03);
		    font-weight: 600;
		    border-bottom: 1px solid var(--border);
		  }
		  .model-dropdown-empty {
		    padding: 14px;
		    text-align: center;
		    color: var(--text-secondary);
		    font-size: 12px;
		  }
		  .model-dropdown-item .model-source-badge {
		    font-size: 10px;
		    padding: 1px 6px;
		    border-radius: 3px;
		    background: var(--card);
		    color: var(--text-secondary);
		    white-space: nowrap;
		  }
  #toast {
    position: fixed;
    top: 18px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 9999;
    max-width: 70vw;
    padding: 10px 18px;
    border-radius: 8px;
    font-size: 13px;
    line-height: 1.6;
    white-space: pre-wrap;
    text-align: center;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.25s;
    background: var(--surface);
    border: 1px solid var(--border);
    box-shadow: 0 6px 24px rgba(0,0,0,0.35);
  }
  #toast.show { opacity: 1; }
  #toast.ok { border-color: var(--success); color: var(--success); }
  #toast.err { border-color: var(--danger); color: var(--danger); }
	  .chat-container {
	    flex: 1;
	    overflow-y: auto;
	    padding: 20px 24px;
	    display: flex;
	    flex-direction: column;
	    gap: 12px;
	  }
	  .message {
	    max-width: 80%;
	    padding: 10px 14px;
	    border-radius: 10px;
	    font-size: 14px;
	    line-height: 1.6;
	    white-space: pre-wrap;
	    word-break: break-word;
	  }
	  .message.user {
	    background: var(--card);
	    align-self: flex-end;
	    border-bottom-right-radius: 4px;
	  }
	  .message.bot {
	    background: var(--surface);
	    align-self: flex-start;
	    border-bottom-left-radius: 4px;
	  }
	  .message.system {
	    background: #2d2d44;
	    align-self: center;
	    font-size: 12px;
	    color: var(--text-secondary);
	    max-width: 90%;
	    text-align: center;
	    border-radius: 6px;
	  }
	  .message.error {
	    background: #3b1a1a;
	    align-self: flex-start;
	    border-left: 3px solid var(--danger);
	  }
	  .message.denied {
	    background: #2d2d1a;
	    align-self: center;
	    font-size: 12px;
	    color: var(--warning);
	    border: 1px solid var(--warning);
	  }
	  .input-area {
	    background: var(--surface);
	    border-top: 1px solid var(--border);
	    padding: 16px 24px;
	    display: flex;
	    gap: 12px;
	    flex-shrink: 0;
	  }
	  .input-area input {
	    flex: 1;
	    background: var(--bg);
	    border: 1px solid var(--border);
	    border-radius: 8px;
	    padding: 12px 16px;
	    color: var(--text);
	    font-size: 14px;
	    outline: none;
	  }
	  .input-area input:focus { border-color: var(--accent); }
	  .input-area input::placeholder { color: var(--text-secondary); }
	  .input-area button {
	    background: var(--accent);
	    color: white;
	    border: none;
	    border-radius: 8px;
	    padding: 12px 24px;
	    font-size: 14px;
	    cursor: pointer;
	  }
	  .input-area button:hover { opacity: 0.85; }
	  .input-area button:disabled { opacity: 0.4; cursor: not-allowed; }
	  /* 生成中的"停止"按钮：红色高亮，DeepSeek 式打断 */
	  .input-area button.stop-mode {
	    background: #e5484d;
	    animation: stop-pulse 1.2s ease-in-out infinite;
	  }
	  @keyframes stop-pulse {
	    0%, 100% { box-shadow: 0 0 0 0 rgba(229, 72, 77, 0.5); }
	    50% { box-shadow: 0 0 0 6px rgba(229, 72, 77, 0); }
	  }
	  .typing {
	    display: none;
	    align-self: flex-start;
	    padding: 10px 14px;
	    color: var(--text-secondary);
	    font-size: 13px;
	  }
	  .typing.active { display: flex; align-items: center; gap: 6px; }
	  .dot {
	    width: 6px; height: 6px;
	    background: var(--text-secondary);
	    border-radius: 50%;
	    animation: bounce 1.4s infinite both;
	  }
	  .dot:nth-child(2) { animation-delay: 0.2s; }
	  .dot:nth-child(3) { animation-delay: 0.4s; }
	  @keyframes bounce { 0%,80%,100% { transform: scale(0.6); } 40% { transform: scale(1); } }
	  .modal-overlay {
	    display: none;
	    position: fixed;
	    top: 0; left: 0; right: 0; bottom: 0;
	    background: rgba(0,0,0,0.7);
	    z-index: 1000;
	    justify-content: center;
	    align-items: center;
	  }
	  .modal-overlay.active { display: flex; }
	  .modal {
	    background: var(--surface);
	    border: 1px solid var(--border);
	    border-radius: 12px;
	    padding: 24px;
	    max-width: 480px;
	    width: 90%;
	    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
	  }
	  .modal h3 { color: var(--warning); margin-bottom: 16px; font-size: 16px; }
	  .modal table { width: 100%; font-size: 13px; margin-bottom: 20px; }
	  .modal td { padding: 6px 8px; color: var(--text-secondary); word-break: break-all; }
	  .modal td:first-child { color: var(--text); font-weight: 600; width: 60px; }
	  .modal-buttons { display: flex; gap: 12px; justify-content: flex-end; }
	  .modal-buttons button { padding: 8px 20px; border: none; border-radius: 6px; font-size: 13px; cursor: pointer; }
	  .btn-allow { background: var(--success); color: #000; }
	  .btn-deny { background: var(--danger); color: white; }
	  .btn-allow:hover, .btn-deny:hover { opacity: 0.85; }
	  .tool-info {
	    font-size: 12px;
	    color: var(--text-secondary);
	    margin-top: 4px;
	    padding: 4px 8px;
	    background: rgba(255,255,255,0.05);
	    border-radius: 4px;
	  }
	  @media (max-width: 768px) {
	    .sidebar { display: none; }
	    .message { max-width: 95%; }
	    .chat-container { padding: 12px; }
	    .input-area { padding: 12px; }
	    .header { padding: 10px 16px; }
	    .header-title { font-size: 16px; }
	  }
	</style>
	</head>
	<body>
	<!-- 侧边栏：会话列表 -->
	<div class="sidebar">
	  <div class="sidebar-header">
	    <h3>会话</h3>
	    <button class="btn-new-session" onclick="newSession()">+ 新对话</button>
	  </div>
	  <div class="session-list" id="session-list"></div>
	</div>
	<!-- 主区域 -->
	<div class="main">
	<div class="header">
	  <div class="header-title">miniyu <span>桌面 AI 助手</span></div>
	  <div class="header-info">
    <span class="badge" id="session-badge">会话: 0</span>
	    <span class="badge" id="provider-badge">离线模式</span>
	    <button id="websearch-toggle" class="websearch-toggle" onclick="toggleWebSearch()"
	            title="联网搜索总开关（DeepSeek 式）：开=百炼端点自动服务端搜索/其他端点保留本地搜索工具；关=完全离线，不注入、不显示本地搜索工具。即时生效，不写入配置文件">🌐 联网: 开</button>
    <div class="model-search-wrapper" id="model-search-wrapper">
      <div class="model-combobox">
        <input type="text" id="model-search-input" class="model-search-input"
               placeholder="搜索模型..."
               title="输入关键词搜索模型，点击下拉选项切换">
        <span class="model-search-clear" id="model-search-clear" onclick="clearModelSearch(event)">×</span>
        <div id="model-dropdown" class="model-dropdown"></div>
      </div>
    </div>
    <select id="authz-select" class="authz-select" onchange="onAuthzChange()" title="授权档位：基础=全部高危需确认(现状)；高级=仅永久删除文件需确认；全自动=从不确认(含删除)">
      <option value="base">基础授权</option>
      <option value="advanced">高级授权</option>
      <option value="full">全自动</option>
    </select>
    <button onclick="resetChat()" style="background:none;border:1px solid var(--border);color:var(--text-secondary);padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px;">重置</button>
  </div>
	</div>
	<div class="chat-container" id="chat"></div>
	<div class="typing" id="typing">
	  <div class="dot"></div><div class="dot"></div><div class="dot"></div>
	  <span style="margin-left:8px;">miniyu 思考中...</span>
	</div>
	<div class="input-area">
	  <input type="text" id="input" placeholder="输入指令，例如：整理桌面、磁盘空间、查看进程..."
	         onkeydown="if(event.key==='Enter') send()" autofocus>
	  <button id="send-btn" onclick="send()">发送</button>
	</div>
	</div>
  <div id="toast"></div>
	<div class="modal-overlay" id="confirm-modal">
	  <div class="modal">
	    <h3>⚠️ miniyu 需要确认</h3>
	    <table id="confirm-table"></table>
	    <div class="modal-buttons">
	      <button class="btn-deny" onclick="confirmDeny()">拒绝</button>
	      <button class="btn-allow" onclick="confirmAllow()">允许</button>
	    </div>
	  </div>
	</div>
	<script>
	let currentTaskId = null;
	let polling = false;
	let eventSource = null;
	let sseActive = false;
	let render = null;
	
	function addMessage(text, role, toolInfo) {
	  const div = document.createElement('div');
	  div.className = 'message ' + role;
	  div.textContent = text;
	  if (toolInfo) {
	    const info = document.createElement('div');
	    info.className = 'tool-info';
	    info.textContent = '🛠 ' + toolInfo;
	    div.appendChild(info);
	  }
	  document.getElementById('chat').appendChild(div);
	  scrollToBottom();
	}
	
	function setLoading(loading) {
	  document.getElementById('input').disabled = loading;
	  const btn = document.getElementById('send-btn');
	  if (loading) {
	    // 生成中：按钮变"停止"，可点击打断（不 disable——否则没法停）
	    btn.disabled = false;
	    btn.textContent = '⏹ 停止';
	    btn.classList.add('stop-mode');
	  } else {
	    btn.disabled = false;
	    btn.textContent = '发送';
	    btn.classList.remove('stop-mode');
	    delete btn.dataset.stopping;
	  }
	  document.getElementById('typing').classList.toggle('active', loading);
	}
	
	function scrollToBottom() {
	  const c = document.getElementById('chat');
	  c.scrollTop = c.scrollHeight;
	}
	
	function clearMessages() {
	  document.getElementById('chat').innerHTML = '';
	}
	
	function resetChat() {
	  clearMessages();
	  polling = false;
	  closeEvents();
	  currentTaskId = null;
	  render = null;
	  setLoading(false);
	  fetch('/reset', { method: 'POST' }).then(() => updateStatus());
	  addMessage('对话已重置', 'system');
	}
	
	// ============================================================
	// 会话管理
	// ============================================================
	
	async function refreshSessions() {
	  try {
	    const resp = await fetch('/sessions');
	    const sessions = await resp.json();
	    const list = document.getElementById('session-list');
	    list.innerHTML = '';
	    sessions.forEach(s => {
	      const item = document.createElement('div');
	      item.className = 'session-item' + (s.is_current ? ' active' : '');
	      item.innerHTML = `
	        <span class="session-item-title">${s.title}</span>
	        <span class="session-item-meta">${s.message_count}条</span>
	        <button class="delete-btn" onclick="event.stopPropagation();deleteSession('${s.id}')">×</button>
	      `;
	      item.onclick = () => switchSession(s.id);
	      list.appendChild(item);
	    });
	    document.getElementById('session-badge').textContent = '会话: ' + sessions.length;
    updateStatus(); // 每次会话列表刷新后同步 在线/离线与模型 徽章（函数声明会提升，可提前调用）
	  } catch (e) {
	    console.error('Failed to load sessions:', e);
	  }
	}
	
	async function newSession() {
	  try {
	    const resp = await fetch('/sessions/new', { method: 'POST' });
	    await resp.json();
	    clearMessages();
	    await refreshSessions();
	    addMessage('新对话已创建', 'system');
	  } catch (e) {
	    console.error('Failed to create session:', e);
	  }
	}
	
	async function switchSession(sessionId) {
	  try {
	    const resp = await fetch('/sessions/' + sessionId, { method: 'POST' });
	    const data = await resp.json();
	    if (data.success) {
	      clearMessages();
	      addMessage('已切换到会话: ' + data.title, 'system');
	      await refreshSessions();
	    }
	  } catch (e) {
	    console.error('Failed to switch session:', e);
	  }
	}
	
	async function deleteSession(sessionId) {
	  if (!confirm('确定删除此会话？')) return;
	  try {
	    const resp = await fetch('/sessions/' + sessionId, { method: 'DELETE' });
	    const data = await resp.json();
	    if (data.success) {
	      clearMessages();
	      await refreshSessions();
	    }
	  } catch (e) {
	    console.error('Failed to delete session:', e);
	  }
	}
	
	// ============================================================
	// 聊天
	// ============================================================
	
	async function send() {
	  const input = document.getElementById('input');
	  const btn = document.getElementById('send-btn');
	  // 生成中：按钮是"⏹ 停止"→ 请求打断当前生成（DeepSeek 式）
	  if (currentTaskId) {
	    if (btn.dataset.stopping !== '1') {
	      btn.dataset.stopping = '1';
	      btn.textContent = '停止中…';
	      btn.disabled = true;  // 防重复点击，SSE done 后 setLoading(false) 恢复
	      try {
	        await fetch('/task/' + currentTaskId + '/stop', { method: 'POST' });
	      } catch (e) { /* 网络异常时等 SSE/轮询超时兜底 */ }
	    }
	    return;
	  }
	  const text = input.value.trim();
	  if (!text) return;
	  input.value = '';
	  addMessage(text, 'user');
	  setLoading(true);
	  try {
	    const resp = await fetch('/chat', {
	      method: 'POST',
	      headers: { 'Content-Type': 'application/json' },
	      body: JSON.stringify({ message: text }),
	    });
	    const data = await resp.json();
    currentTaskId = data.task_id;
    // 优先 SSE 事件流（实时思考/逐字正文/工具步骤），环境不支持时回退轮询
    if (typeof EventSource !== 'undefined') connectEvents(currentTaskId);
    else startPolling();
  } catch (e) {
    addMessage('网络错误: ' + e.message, 'error');
	    setLoading(false);
	  }
	}
	
	// ============================================================
// SSE 事件流（DeepSeek 式实时展示）
// ============================================================

function closeEvents() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  sseActive = false;
}

function connectEvents(taskId) {
  closeEvents();
  render = { thinkingStart: null, thinkingEl: null, thinkingContent: null,
             thinkingTimeEl: null, thinkingLabelEl: null, thinkingDone: false,
             answerEl: null, finished: false };
  const es = new EventSource('/task/' + taskId + '/events');
  eventSource = es;
  es.onmessage = function (e) {
    let data;
    try { data = JSON.parse(e.data); } catch (err) { return; }
    sseActive = true;
    switch (data.type) {
      case 'reasoning': handleReasoning(data.delta); break;
      case 'token': handleToken(data.delta); break;
      case 'tool_call': handleToolCall(data.tool, data.arguments); break;
      case 'confirm': showConfirmModal(data); break;
      case 'done': settleTask('done', data.result); break;
      case 'error': settleTask('error', data.result); break;
    }
  };
  es.onerror = function () {
    if (!currentTaskId || (render && render.finished)) return;
    // 从未收到事件：SSE 不可用（旧代理/服务器异常）→ 回退轮询
    if (!sseActive) { closeEvents(); startPolling(); return; }
    // 流中途断开：查一次任务状态兜底（终态事件可能在断线瞬间丢失）
    fetch('/task/' + currentTaskId).then(function (r) { return r.json(); }).then(function (d) {
      if (render && render.finished) return;
      if (d.status === 'done' || d.status === 'error') {
        if (d.result === '任务不存在') {
          settleTask('done', (render && render.answerEl)
            ? render.answerEl.textContent : '任务已结束（连接中断，结果丢失）');
        } else {
          settleTask(d.status, d.result);
        }
      }
    }).catch(function () {});
  };
}

// 思考 token：实时滚动展示"思考中 (Xs)"
function handleReasoning(delta) {
  if (!render.thinkingEl) {
    render.thinkingStart = Date.now();
    const panel = document.createElement('div');
    panel.className = 'message thinking';
    const header = document.createElement('div');
    header.className = 'thinking-header';
    const label = document.createElement('span');
    label.className = 'thinking-label';
    label.textContent = '思考中';
    const time = document.createElement('span');
    time.className = 'thinking-time';
    const toggle = document.createElement('span');
    toggle.className = 'thinking-toggle';
    toggle.textContent = '▼';
    header.appendChild(label);
    header.appendChild(time);
    header.appendChild(toggle);
    header.onclick = function () { panel.classList.toggle('collapsed'); };
    const content = document.createElement('div');
    content.className = 'thinking-content';
    panel.appendChild(header);
    panel.appendChild(content);
    document.getElementById('chat').appendChild(panel);
    render.thinkingEl = panel;
    render.thinkingContent = content;
    render.thinkingTimeEl = time;
    render.thinkingLabelEl = label;
    document.getElementById('typing').classList.remove('active');  // 思考面板接管加载提示
  }
  render.thinkingContent.textContent += delta;
  const secs = Math.max(1, Math.round((Date.now() - render.thinkingStart) / 1000));
  render.thinkingTimeEl.textContent = '（' + secs + 's）';
  render.thinkingContent.scrollTop = render.thinkingContent.scrollHeight;  // 思考滚动跟随
  scrollToBottom();
}

// 思考结束：折叠为"已深度思考（用时 X 秒）"，点标题可回看
function completeThinking() {
  if (!render || !render.thinkingEl || render.thinkingDone) return;
  render.thinkingDone = true;
  const secs = Math.max(1, Math.round((Date.now() - render.thinkingStart) / 1000));
  render.thinkingLabelEl.textContent = '已深度思考';
  render.thinkingTimeEl.textContent = '（用时 ' + secs + ' 秒）';
  render.thinkingEl.classList.add('done');
  render.thinkingEl.classList.add('collapsed');
}

// 正文 token：逐字输出
function handleToken(delta) {
  if (!render.answerEl) {
    completeThinking();
    const div = document.createElement('div');
    div.className = 'message bot';
    document.getElementById('chat').appendChild(div);
    render.answerEl = div;
    document.getElementById('typing').classList.remove('active');
  }
  render.answerEl.textContent += delta;
  scrollToBottom();
}

// 工具调用：显示执行步骤行（参数超长截断）
function handleToolCall(tool, args) {
  let s = '🛠 ' + tool;
  if (args && Object.keys(args).length) {
    let a = JSON.stringify(args);
    if (a.length > 100) a = a.slice(0, 100) + '…';
    s += '  ' + a;
  }
  const line = document.createElement('div');
  line.className = 'message tool-line';
  line.textContent = s;
  document.getElementById('chat').appendChild(line);
  scrollToBottom();
}

// 统一收尾：SSE done/error 与轮询 done/error 都走这里（幂等）
function settleTask(status, result) {
  if (render && render.finished) return;
  if (render) render.finished = true;
  closeEvents();
  polling = false;
  currentTaskId = null;
  completeThinking();
  if (render && render.answerEl) {
    if (result) render.answerEl.textContent = result;  // 以最终全文校准，防流中断截断
  } else {
    const role = status === 'error' ? 'error'
      : (result && result.indexOf('已取消') !== -1 ? 'denied' : 'bot');
    addMessage(result || '（无输出）', role);
  }
  setLoading(false);
  refreshSessions();
}

async function startPolling() {
  if (!currentTaskId) return;
  polling = true;
	  while (polling) {
	    try {
	      const resp = await fetch('/task/' + currentTaskId);
	      const data = await resp.json();
	      if (data.status === 'confirm') {
	        showConfirmModal(data);
	        return;
	      } else if (data.status === 'done') {
        settleTask('done', data.result);
        return;
      } else if (data.status === 'error') {
        settleTask('error', data.result);
        return;
      }
	      await new Promise(r => setTimeout(r, 500));
	    } catch (e) {
	      addMessage('轮询错误: ' + e.message, 'error');
	      polling = false;
	      setLoading(false);
	      return;
	    }
	  }
	}
	
	function showConfirmModal(data) {
	  const table = document.getElementById('confirm-table');
	  table.innerHTML = '';
	  const rows = [
	    ['工具', data.tool || '?'],
	    ['风险', data.risk || 'high'],
	    ['说明', data.description || '?'],
	    ['参数', JSON.stringify(data.arguments || {})],
	  ];
	  rows.forEach(([k, v]) => {
	    const tr = document.createElement('tr');
	    const td1 = document.createElement('td'); td1.textContent = k;
	    const td2 = document.createElement('td'); td2.textContent = v;
	    tr.appendChild(td1); tr.appendChild(td2);
	    table.appendChild(tr);
	  });
	  document.getElementById('confirm-modal').classList.add('active');
	}
	
	async function confirmAllow() {
	  document.getElementById('confirm-modal').classList.remove('active');
	  try {
	    await fetch('/confirm/' + currentTaskId, {
	      method: 'POST',
	      headers: { 'Content-Type': 'application/json' },
	      body: JSON.stringify({ allow: true }),
	    });
	    startPolling();
	  } catch (e) {
	    addMessage('错误: ' + e.message, 'error');
	    setLoading(false);
	  }
	}
	
	async function confirmDeny() {
	  document.getElementById('confirm-modal').classList.remove('active');
	  try {
	    await fetch('/confirm/' + currentTaskId, {
	      method: 'POST',
	      headers: { 'Content-Type': 'application/json' },
	      body: JSON.stringify({ allow: false }),
	    });
	    addMessage('⛔ 高危操作已拒绝', 'denied');
	    polling = false;
	    currentTaskId = null;
	    setLoading(false);
	  } catch (e) {
	    addMessage('错误: ' + e.message, 'error');
	    setLoading(false);
	  }
	}
	
	// 顶栏徽章：根据后端 /status 实时刷新（离线/在线、会话数），并把当前模型同步到搜索框
	async function updateStatus() {
	  let data = null;
	  try {
	    const resp = await fetch('/status');
	    data = await resp.json();
	  } catch (e) {
	    document.getElementById('provider-badge').textContent = '状态未知';
	    return null;
	  }
	  const offline = data.provider === 'deterministic';
	  document.getElementById('provider-badge').textContent =
	    offline ? '🧠 离线模式' : '☁️ 在线模式';
	  if (data.authz !== undefined) setAuthzControl(data.authz);
	  if (data.web_search !== undefined) setWebSearchControl(data.web_search);
	  if (data.session_count !== undefined) {
	    document.getElementById('session-badge').textContent = '会话: ' + data.session_count;
	  }
	  loadModels();
	  return data;
	}

// 联网搜索总开关：状态渲染 + 点击切换（POST /toggle-web-search，即时生效）
function setWebSearchControl(on) {
  const btn = document.getElementById('websearch-toggle');
  if (!btn) return;
  btn.textContent = on ? '🌐 联网: 开' : '🚫 联网: 关';
  btn.classList.toggle('on', !!on);
}

async function toggleWebSearch() {
  const btn = document.getElementById('websearch-toggle');
  if (!btn) return;
  btn.disabled = true;
  try {
    const r = await fetch('/toggle-web-search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const d = await r.json();
    showToast(d.message, !!d.ok);
    setWebSearchControl(!!d.web_search);
  } catch (e) {
    showToast('切换联网搜索出错：' + e.message, false);
    await updateStatus();
  } finally {
    btn.disabled = false;
  }
}
	
	function showToast(message, ok) {
	  const t = document.getElementById('toast');
	  if (!t) return;
	  t.textContent = message;
	  t.className = 'toast ' + (ok ? 'ok' : 'err');
	  t.classList.add('show');
	  clearTimeout(showToast._timer);
	  showToast._timer = setTimeout(function () { t.classList.remove('show'); }, 8000);
	}

// 授权档位下拉：当前值来自 /status 的 authz 字段；切「全自动」先弹一次浏览器确认把关
let lastAuthz = 'base';

function setAuthzControl(level) {
  const sel = document.getElementById('authz-select');
  if (!sel) return;
  const v = (['base', 'advanced', 'full'].indexOf(level) >= 0) ? level : 'base';
  lastAuthz = v;
  sel.value = v;
}

async function onAuthzChange() {
  const sel = document.getElementById('authz-select');
  const level = sel.value;
  if (!level) return;
  if (level === 'full') {
    const ok = window.confirm('切到「全自动」后，miniyu 将不再确认任何操作（含文件删除）。确定切换？');
    if (!ok) {
      setAuthzControl(lastAuthz);
      return;
    }
  }
  sel.disabled = true;
  try {
    const r = await fetch('/switch-authz', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level: level }),
    });
    const d = await r.json();
    showToast(d.message, !!d.ok);
    await updateStatus();
  } catch (e) {
    showToast('切换授权出错：' + e.message, false);
    await updateStatus();
  } finally {
    sel.disabled = false;
  }
}

// ============================================================
// 可搜索模型下拉框（Combobox）
// 输入即搜索过滤，点击下拉选项即时切换模型
// ============================================================

var _modelList = []; // {value, label, source, group, active}

async function loadModels() {
  var input = document.getElementById('model-search-input');
  if (!input) return;
  try {
    var [onlineResp, fallbackResp] = await Promise.all([
      fetch('/api/models'),
      fetch('/api/fallback-models'),
    ]);
    var online = await onlineResp.json();
    var fallback = await fallbackResp.json();

    _modelList = [];

    if (online.online && online.models && online.models.length) {
      online.models.forEach(function (m) {
        _modelList.push({
          value: 'online:' + m,
          label: m,
          source: 'online',
          group: '☁️ 在线 API 模型',
          active: m === online.current,
        });
      });
    }
    if (fallback.models && fallback.models.length) {
      fallback.models.forEach(function (m) {
        _modelList.push({
          value: 'fallback:' + m,
          label: m,
          source: 'fallback',
          group: '🖥️ 本地 Ollama 模型',
          active: m === fallback.current,
        });
      });
    }

    // 设置当前选中模型
    var active = null;
    for (var i = 0; i < _modelList.length; i++) {
      if (_modelList[i].active) { active = _modelList[i]; break; }
    }
    if (active) {
      input.value = active.label;
      input.dataset.value = active.value;
      document.getElementById('model-search-clear').style.display = 'block';
    } else {
      input.value = '';
      input.dataset.value = '';
      document.getElementById('model-search-clear').style.display = 'none';
    }

    input.disabled = (_modelList.length === 0);
    renderDropdown(input.value);
  } catch (e) {
    _modelList = [];
    input.value = '';
    input.dataset.value = '';
    input.disabled = true;
    var dd = document.getElementById('model-dropdown');
    if (dd) dd.innerHTML = '<div class="model-dropdown-empty">模型列表加载失败</div>';
  }
}

function renderDropdown(query) {
  var dropdown = document.getElementById('model-dropdown');
  if (!dropdown) return;
  var q = (query || '').toLowerCase().trim();

  var filtered = _modelList;
  if (q) {
    filtered = _modelList.filter(function (m) { return m.label.toLowerCase().indexOf(q) !== -1; });
  }

  if (filtered.length === 0) {
    dropdown.innerHTML = '<div class="model-dropdown-empty">' + (q ? '无匹配模型' : '无可用模型') + '</div>';
    return;
  }

  // 按来源分组
  var groups = {};
  filtered.forEach(function (m) {
    if (!groups[m.group]) groups[m.group] = [];
    groups[m.group].push(m);
  });

  var html = '';
  // 在线模型在前，本地在后
  var groupKeys = Object.keys(groups).sort(function (a, b) {
    if (a.indexOf('在线') !== -1 && b.indexOf('本地') !== -1) return -1;
    if (a.indexOf('在线') !== -1 && b.indexOf('在线') !== -1) return 0;
    if (a.indexOf('本地') !== -1 && b.indexOf('在线') !== -1) return 1;
    return 0;
  });
  groupKeys.forEach(function (groupLabel) {
    html += '<div class="model-dropdown-group-label">' + groupLabel + '</div>';
    groups[groupLabel].forEach(function (m) {
      var activeClass = m.active ? ' active' : '';
      var badge = m.source === 'online' ? '☁️ API' : '🖥️ 本地';
      var safeLabel = m.label.replace(/"/g, '&quot;');
      html += '<div class="model-dropdown-item' + activeClass + '" data-value="' + m.value + '" data-label="' + safeLabel + '">' +
              '<span>' + m.label + '</span>' +
              '<span class="model-source-badge">' + badge + '</span>' +
              '</div>';
    });
  });
  dropdown.innerHTML = html;

  // 绑定点击事件（事件委托——使用 mousedown + 阻止默认行为）
  // 不能用 click，因为 blur 在 click 之前触发会隐藏下拉框
  var items = dropdown.querySelectorAll('.model-dropdown-item');
  for (var i = 0; i < items.length; i++) {
    items[i].addEventListener('mousedown', function (e) {
      e.preventDefault();
      selectModel(this.getAttribute('data-value'), this.getAttribute('data-label'));
    });
  }
}

function onModelSearchInput() {
  var input = document.getElementById('model-search-input');
  var clear = document.getElementById('model-search-clear');
  var dropdown = document.getElementById('model-dropdown');

  if (input.value) {
    clear.style.display = 'block';
  } else {
    clear.style.display = 'none';
  }
  input.dataset.value = '';
  renderDropdown(input.value);
  dropdown.classList.add('show');
}

function onModelSearchFocus() {
  var dropdown = document.getElementById('model-dropdown');
  if (_modelList.length > 0) {
    renderDropdown(document.getElementById('model-search-input').value);
    dropdown.classList.add('show');
  }
}

function onModelSearchBlur() {
  setTimeout(function () {
    document.getElementById('model-dropdown').classList.remove('show');
  }, 200);
}

function onModelSearchKeydown(e) {
  if (e.key === 'Enter') {
    var input = document.getElementById('model-search-input');
    var q = input.value.toLowerCase().trim();
    var match = null;
    for (var i = 0; i < _modelList.length; i++) {
      if (_modelList[i].label.toLowerCase().indexOf(q) !== -1) {
        match = _modelList[i];
        break;
      }
    }
    if (match) {
      selectModel(match.value, match.label);
    }
  } else if (e.key === 'Escape') {
    document.getElementById('model-dropdown').classList.remove('show');
  }
}

function clearModelSearch(e) {
  if (e) e.stopPropagation();
  var input = document.getElementById('model-search-input');
  input.value = '';
  input.dataset.value = '';
  document.getElementById('model-search-clear').style.display = 'none';
  renderDropdown('');
  document.getElementById('model-dropdown').classList.remove('show');
  input.focus();
}

function selectModel(value, label) {
  var input = document.getElementById('model-search-input');
  input.value = label;
  input.dataset.value = value;
  document.getElementById('model-dropdown').classList.remove('show');
  document.getElementById('model-search-clear').style.display = 'block';

  input.disabled = true;

  if (value.indexOf('online:') === 0) {
    var model = value.substring(7);
    fetch('/switch-model', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: model }),
    })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      showToast(d.message, !!d.ok);
      loadModels();
      updateStatus();
    })
    .catch(function (e) { showToast('切换出错：' + e.message, false); })
    .finally(function () { input.disabled = false; });
  } else if (value.indexOf('fallback:') === 0) {
    var model = value.substring(9);
    fetch('/switch-fallback-model', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: model }),
    })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      showToast(d.message, !!d.ok);
      loadModels();
      updateStatus();
    })
    .catch(function (e) { showToast('切换出错：' + e.message, false); })
    .finally(function () { input.disabled = false; });
  }
}

// 初始化
		fetch('/status').then(r => r.json()).then(data => {
		  document.getElementById('provider-badge').textContent =
		    data.provider === 'deterministic' ? '🧠 离线模式' : '☁️ 在线模式';
		  setAuthzControl(data.authz);
		  if (data.provider === 'deterministic') {
		    addMessage('你好，我是 miniyu，你的桌面 AI 助手。', 'bot');
		    addMessage('当前为离线模式，可以执行整理桌面、磁盘空间、查看进程等预设指令。', 'system');
		    addMessage('⚠️ 如需处理复杂任务，建议配置 API key 使用真实 LLM。\n编辑 config.yaml 设置 provider: openai_compatible 即可。', 'system');
		  } else {
		    addMessage('你好，我是 miniyu，你的桌面 AI 助手。', 'bot');
		    addMessage('已连接 ' + data.model + '，随时为你服务。', 'system');
		  }
		  refreshSessions();
		  loadModels();
		});

		// 绑定模型搜索输入框事件
		var modelInput = document.getElementById('model-search-input');
		if (modelInput) {
		  modelInput.addEventListener('input', onModelSearchInput);
		  modelInput.addEventListener('focus', onModelSearchFocus);
		  modelInput.addEventListener('blur', onModelSearchBlur);
		  modelInput.addEventListener('keydown', onModelSearchKeydown);
		}
	</script>
</body>
</html>"""


# ============================================================
# Flask 路由
# ============================================================

# 添加 CORS 支持（处理 OPTIONS 预检请求）
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

@app.route("/", methods=["GET", "OPTIONS"])
def index():
    return HTML


@app.route("/status", methods=["GET"])
def status():
    cfg = load_config()
    llm = cfg.get("llm", {})
    # 真机已配置（provider != deterministic）但 agent 尚未创建：立即按真实状态上报，
    # 避免页面一进来就误显示"离线模式 / 离线脑"
    if _agent is None and llm.get("provider", "deterministic") != "deterministic":
        try:
            _ensure_agent()
        except Exception as e:
            return jsonify({
                "provider": llm.get("provider", "deterministic"),
                "model": llm.get("model") or "未配置模型",
                "vision": bool(llm.get("supports_vision", False)),
                "authz": resolve_authz_level(cfg.get("agent", {})),
                "web_search": bool(cfg.get("web_search", {}).get("enabled", True)),
                "tools": 0,
                "session_id": None,
                "session_title": "新对话",
                "session_count": 0,
                "error": str(e),
            })
    if _agent is None:
        return jsonify({
            "provider": "deterministic",
            "model": "离线脑（规则匹配）",
            "vision": False,
            "authz": resolve_authz_level(cfg.get("agent", {})),
            "web_search": bool(cfg.get("web_search", {}).get("enabled", True)),
            "tools": 0,
            "session_id": None,
            "session_title": "新对话",
            "session_count": 0,
        })
    current = _agent.conversation
    return jsonify({
        "provider": _agent.provider,
        "model": _agent.model_name,
        "vision": _agent.llm.supports_vision,
        "authz": _agent.authz_level,
        "web_search": _agent.web_search_enabled,
        "tools": len(_agent.api.list_tools_mcp()),
        "session_id": _agent.current_session_id,
        "session_title": current.title if current else "新对话",
        "session_count": len(_agent.list_sessions()),
    })


@app.route("/toggle-web-search", methods=["POST", "OPTIONS"])
def toggle_web_search():
    """运行时切换联网搜索总开关（DeepSeek 式；管百炼服务端搜索 + 本地 browser_search/browser_extract）。

    不回写 config.yaml——本文件是出厂默认，会话内切换即时生效，重启后恢复默认。
    """
    if _agent is None:
        _ensure_agent()
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if enabled is None:
        enabled = not _agent.web_search_enabled
    _agent.set_web_search_enabled(bool(enabled))
    state = "已开启" if _agent.web_search_enabled else "已关闭"
    return jsonify({"ok": True, "web_search": _agent.web_search_enabled,
                    "message": f"联网搜索{state}（即时生效，不写入配置文件）"})


@app.route("/api/models", methods=["GET", "OPTIONS"])
def api_models():
    cfg = load_config()
    llm = cfg.get("llm", {})
    online = (
        llm.get("provider", "deterministic") != "deterministic"
        and bool(llm.get("base_url"))
        and bool(llm.get("api_key"))
    )
    # 获取纯模型名称（去除标签）
    current_raw = llm.get("model") or ""
    if _agent is not None and hasattr(_agent, "llm"):
        llm_client = _agent.llm
        if isinstance(llm_client, FailoverClient):
            # 从 primary 获取当前模型
            if hasattr(llm_client.primary, "model"):
                current_raw = llm_client.primary.model
        elif hasattr(llm_client, "model"):
            current_raw = llm_client.model
    current = current_raw
    if not online:
        return jsonify({
            "online": False,
            "current": None,
            "models": [],
            "error": None,
            "message": "当前未接入 API（离线模式或未配置 base_url/api_key）。请先在 config.yaml 配置后再切换模型。",
        })
    info = list_available_models(cfg)
    models = info.get("models") or list(FALLBACK_CANDIDATES)
    if current and current not in models:
        models = [current] + models
    resp = {
        "online": True,
        "current": current,
        "models": models,
        "error": info.get("error"),
    }
    if info.get("error"):
        resp["message"] = "从 API 拉取模型列表失败，已显示常用候选；切换时会实测连通性，不通不给用。"
    return jsonify(resp)


@app.route("/switch-model", methods=["POST"])
def switch_model():
    global _agent
    data = request.get_json(silent=True) or {}
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"ok": False, "message": "请先选择要切换的模型。"})

    cfg = load_config()
    llm = cfg.get("llm", {})
    if llm.get("provider", "deterministic") == "deterministic" or not (
        llm.get("base_url") and llm.get("api_key")
    ):
        return jsonify({
            "ok": False,
            "message": "当前是离线模式（或未配置 API）。请先在 config.yaml 里配置 provider/base_url/api_key，接入 API 后才能切换模型。",
        })

    effective = _agent.model_name if _agent is not None else (llm.get("model") or "")
    if model == effective:
        return jsonify({"ok": True, "message": f"当前已经在使用 {model}，无需重复切换。"})

    # 1) 先实测连通性：连通才允许切换
    probe = probe_model(cfg, model)
    if not probe["ok"]:
        return jsonify({
            "ok": False,
            "model": model,
            "message": f"❌ 切换失败：{model} 未连通（{probe['message']}）。\n请换个模型试试。",
        })

    # 2) 持久化到 config.yaml（文本级改 model 行，保留中文注释）
    try:
        set_config_model(model)
    except Exception as e:
        return jsonify({
            "ok": False,
            "model": model,
            "message": f"模型 {model} 连通正常，但写入配置失败：{e}。\n请手动编辑 config.yaml 的 model 后重启。",
        })

    # 3) 热切换运行中的 agent（不重建，当前会话与上下文不丢）
    # 支持 FailoverClient（primary 承载模型）和普通 OpenAICompatibleClient 两种模式
    if _agent is not None:
        llm_client = _agent.llm
        if isinstance(llm_client, FailoverClient) and hasattr(llm_client.primary, "model"):
            llm_client.primary.model = model
            if hasattr(llm_client.primary, "reset_runtime_flags"):
                llm_client.primary.reset_runtime_flags()  # 新模型的工具能力需重新探测
            # 切换回在线模型时关闭 force_local 模式
            if hasattr(llm_client, "force_local_mode"):
                llm_client.force_local_mode(False)
        elif hasattr(llm_client, "model"):
            llm_client.model = model
            if hasattr(llm_client, "reset_runtime_flags"):
                llm_client.reset_runtime_flags()
        _agent.config.setdefault("llm", {})["model"] = model

    return jsonify({
        "ok": True,
        "model": model,
        "message": f"✅ 切换成功：模型 {model} 已连通，可正常使用。",
    })


@app.route("/api/fallback-models", methods=["GET"])
def api_fallback_models():
    """返回可用的本地 Ollama 模型列表"""
    cfg = load_config()
    fallback_cfg = cfg.get("llm", {}).get("fallback", {})
    base_url = fallback_cfg.get("base_url", "http://localhost:11434/v1").rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    configured = fallback_cfg.get("models", [])
    # 优先从运行中的 Agent 获取当前 fallback 模型（热切换后比 config 文件更准确）
    current = fallback_cfg.get("model", "")
    if _agent is not None and isinstance(_agent.llm, FailoverClient):
        fb = _agent.llm.fallback
        if fb and hasattr(fb, "model") and fb.model:
            current = fb.model

    # 尝试从 Ollama 的 /api/tags 拉取实际安装的模型
    local_models = []
    try:
        import requests
        tags_url = base_url.replace("/v1", "/api/tags")
        resp = requests.get(tags_url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            local_models = [m["name"] for m in data.get("models", []) if m.get("name")]
    except Exception:
        pass

    # 优先使用配置中的模型列表，再补充 Ollama 实际安装的
    all_models = list(configured)
    for m in local_models:
        if m not in all_models:
            all_models.append(m)

    return jsonify({
        "models": all_models,
        "current": current,
        "local_models": local_models,
    })


@app.route("/switch-fallback-model", methods=["POST"])
def switch_fallback_model():
    """切换备用 LLM 模型（本地 Ollama 模型热切换，不重建 Agent）"""
    global _agent
    data = request.get_json(silent=True) or {}
    model = (data.get("model") or "").strip()
    if not model:
        return jsonify({"ok": False, "message": "请指定要切换的模型名称。"})

    # 1) 持久化到 config.yaml
    cfg = load_config()
    fallback_cfg = cfg.setdefault("llm", {}).setdefault("fallback", {})
    if model == fallback_cfg.get("model", ""):
        return jsonify({"ok": True, "message": f"当前已经在使用 {model}，无需重复切换。"})

    import yaml
    try:
        config_path = cfg.get("_config_path", "config.yaml")
        with open(config_path, "r", encoding="utf-8") as f:
            raw = f.read()
        import re
        # 替换 fallback 段的 model 行（4 空格缩进，区别于主模型的 2 空格）
        raw = re.sub(
            r'^(    model:\s*)"[^"]*"',
            lambda m: m.group(1) + f'"{model}"',
            raw,
            count=1,
            flags=re.MULTILINE,
        )
        with open(config_path, "w", encoding="utf-8") as f:
            f.write(raw)
    except Exception as e:
        return jsonify({
            "ok": False,
            "message": f"写入配置失败：{e}。请手动编辑 config.yaml 的 fallback.model。",
        })

    # 2) 热切换运行中的 Agent（不重建）
    if _agent is not None:
        _agent.switch_fallback_model(model)
        # 更新 agent 内部 config 缓存
        _agent.config.setdefault("llm", {}).setdefault("fallback", {})["model"] = model
        # 切换本地模型时自动启用 force_local 模式，直接用本地模型对话
        if hasattr(_agent.llm, "force_local_mode"):
            _agent.llm.force_local_mode(True)

    return jsonify({
        "ok": True,
        "model": model,
        "message": f"✅ 已切换到本地模型 {model}，对话将直接使用该模型。",
    })


@app.route("/switch-authz", methods=["POST"])
def switch_authz():
    """切换授权档位（base/advanced/full）：持久化 config.yaml + 热改运行中 agent。

    档位只影响"哪些高危操作要弹确认"，与模型连通性无关，离线/在线都能切。
    """
    global _agent
    data = request.get_json(silent=True) or {}
    level = (data.get("level") or "").strip()
    if level not in AUTHZ_LEVELS:
        return jsonify({
            "ok": False,
            "message": f"未知授权档位：{level or '(空)'}（可选 base / advanced / full）。",
        })

    cfg = load_config()
    current = (
        _agent.authz_level if _agent is not None
        else resolve_authz_level(cfg.get("agent", {}))
    )
    if level == current:
        return jsonify({
            "ok": True,
            "level": level,
            "message": f"当前已在「{AUTHZ_LABELS[level]}」，无需重复切换。",
        })

    # 1) 持久化到 config.yaml（文本级替换 agent.authorization 行，保留中文注释）
    try:
        set_config_authorization(level)
    except Exception as e:
        return jsonify({
            "ok": False,
            "level": current,
            "message": f"授权档位写回 config.yaml 失败：{e}。\n请手动编辑 config.yaml 的 agent.authorization 后重启。",
        })

    # 2) 热切换运行中的 agent（不重建，当前会话与上下文不丢）
    if _agent is not None:
        _agent.authz_level = level
        _agent.config.setdefault("agent", {})["authorization"] = level

    tip = {
        "advanced": "（高级授权：仅永久删除文件需确认）",
        "full": "（全自动：不再确认任何操作）",
        "base": "（基础授权：全部高危操作需确认）",
    }[level]
    return jsonify({
        "ok": True,
        "level": level,
        "message": f"✅ 已切换为「{AUTHZ_LABELS[level]}」授权{tip}",
    })


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"error": "请输入指令。"})

    task_id = str(uuid.uuid4())[:8]

    # 初始化任务状态（events = SSE 事件队列，前端 /task/<id>/events 消费；
    # stop_event 供"停止生成"按钮打断流式输出）
    with _tasks_lock:
        _tasks[task_id] = {"status": "pending", "result": None,
                           "events": queue.Queue(),
                           "stop_event": threading.Event()}

    # 启动后台 Agent 线程
    thread = threading.Thread(
        target=_run_agent_thread,
        args=(task_id, message),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id})


@app.route("/task/<task_id>/stop", methods=["POST", "OPTIONS"])
def stop_task(task_id):
    """停止生成：置位 stop_event，agent 在下一个流式 chunk 间隙终止。
    已流出的部分文本会作为最终回复保留（DeepSeek 式打断体验）。"""
    if request.method == "OPTIONS":
        return ("", 204)
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return jsonify({"ok": False, "message": "任务不存在或已结束。"})
        if task.get("status") in ("done", "error"):
            return jsonify({"ok": False, "message": "任务已结束，无需停止。"})
        stop_event = task.get("stop_event")
        if stop_event is None:
            return jsonify({"ok": False, "message": "该任务不支持停止。"})
        stop_event.set()
    return jsonify({"ok": True, "message": "已请求停止生成。"})


@app.route("/task/<task_id>/events")
def task_events(task_id):
    """
    SSE 流式端点：转发后台 Agent 的过程事件。

    对标 ChatGPT/DeepSeek 的流式体验：
      思考 token 实时滚动 → 结束折叠为"已深度思考（用时 Xs）"；
      正文逐字输出；工具调用显示执行步骤；高危确认弹框。
    15s 无事件发心跳注释行，防止代理层断开空闲连接。
    """
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return jsonify({"error": "任务不存在"}), 404
        eq = task.get("events")

    def gen():
        while True:
            try:
                item = eq.get(timeout=15)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("type") in ("done", "error"):
                with _tasks_lock:
                    _tasks.pop(task_id, None)
                return

    return Response(
        gen(),
        content_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/task/<task_id>", methods=["GET"])
def task_status(task_id):
    with _tasks_lock:
        task = _tasks.get(task_id)

    if task is None:
        return jsonify({"status": "error", "result": "任务不存在"})

    if task["status"] == "confirm":
        return jsonify({
            "status": "confirm",
            "tool": task["confirm_data"]["tool"],
            "description": task["confirm_data"]["description"],
            "arguments": task["confirm_data"]["arguments"],
            "risk": task["confirm_data"]["risk"],
        })

    if task["status"] == "done":
        # 清理已完成的任务
        with _tasks_lock:
            result = task["result"]
            del _tasks[task_id]
        return jsonify({"status": "done", "result": result})

    if task["status"] == "error":
        with _tasks_lock:
            result = task["result"]
            del _tasks[task_id]
        return jsonify({"status": "error", "result": result})

    return jsonify({"status": "pending"})


@app.route("/confirm/<task_id>", methods=["POST"])
def confirm_task(task_id):
    data = request.get_json()
    allow = data.get("allow", False)

    with _tasks_lock:
        task = _tasks.get(task_id)

    if task is None:
        return jsonify({"error": "任务不存在"})

    if task["status"] != "confirm":
        return jsonify({"error": "任务无需确认"})

    task["confirm_result"] = allow
    task["confirm_event"].set()

    return jsonify({"success": True})


@app.route("/reset", methods=["POST"])
def reset():
    global _agent
    with _agent_lock:
        if _agent:
            _agent.reset()
    return jsonify({"success": True})


# ============================================================
# 会话管理 API（对标 ChatGPT 的会话切换）
# ============================================================

@app.route("/sessions", methods=["GET"])
def list_sessions():
    global _agent
    with _agent_lock:
        if _agent is None:
            return jsonify([])
        return jsonify(_agent.list_sessions())


@app.route("/sessions/new", methods=["POST"])
def new_session():
    global _agent
    with _agent_lock:
        if _agent is None:
            _agent = Agent(
                config=load_config(),
                confirm_handler=_web_confirm_handler,
            )
        sid = _agent.create_session()
    return jsonify({"session_id": sid, "title": "新对话"})


@app.route("/sessions/<session_id>", methods=["POST"])
def switch_session(session_id):
    global _agent
    with _agent_lock:
        if _agent is None:
            return jsonify({"error": "Agent 未初始化"}), 400
        if _agent.switch_session(session_id):
            current = _agent.conversation
            return jsonify({"success": True, "session_id": session_id, "title": current.title, "message_count": current.total_messages})
        return jsonify({"error": "会话不存在"}), 404


@app.route("/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    global _agent
    with _agent_lock:
        if _agent is None:
            return jsonify({"error": "Agent 未初始化"}), 400
        if _agent.delete_session(session_id):
            return jsonify({"success": True})
        return jsonify({"error": "会话不存在"}), 404


@app.route("/sessions/<session_id>/rename", methods=["POST"])
def rename_session(session_id):
    global _agent
    data = request.get_json()
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    with _agent_lock:
        if _agent is None:
            return jsonify({"error": "Agent 未初始化"}), 400
        if _agent.rename_session(session_id, title):
            return jsonify({"success": True, "title": title})
        return jsonify({"error": "会话不存在"}), 404


# ============================================================
# 启动
# ============================================================

def main():
    import webbrowser
    port = 5000
    print()
    print("  ╔══════════════════════════════════════════╗")
    print("  ║        miniyu — 桌面 AI 助手            ║")
    print("  ╠══════════════════════════════════════════╣")
    print(f"  ║  打开浏览器访问:                        ║")
    print(f"  ║  http://localhost:{port}                  ║")
    print("  ║                                          ║")
    print("  ║  Ctrl+C 退出                             ║")
    print("  ╚══════════════════════════════════════════╝")
    print()
    print("  miniyu 启动成功！")
    print()
    webbrowser.open(f"http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()