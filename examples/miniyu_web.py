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
import threading
import uuid
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, request, jsonify

from core.agent import Agent
from core.agent_config import load_config, set_config_model, set_config_authorization
from core.llm_client import list_available_models, probe_model, FALLBACK_CANDIDATES
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
#             "confirm_result": bool}}
_tasks = {}
_tasks_lock = threading.Lock()


# ============================================================
# 确认处理器（线程安全，异步阻塞）
# ============================================================

def _web_confirm_handler(pv):
    """
    Web 确认处理器。

    在 Agent.run() 的线程中被调用。
    1. 存储确认数据到共享状态
    2. 阻塞等待前端通过 /confirm 接口放行/拒绝
    """
    task_id = _get_current_task_id()
    if not task_id:
        raise ConfirmationDenied(pv.get("name", ""))

    event = threading.Event()
    with _tasks_lock:
        _tasks[task_id] = {
            "status": "confirm",
            "confirm_data": {
                "tool": pv.get("name", ""),
                "description": pv.get("description", ""),
                "arguments": pv.get("params", {}),
                "risk": pv.get("risk", "high"),
            },
            "confirm_event": event,
            "confirm_result": None,
            "result": None,
        }

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
    """在后台线程中执行 Agent.run()"""
    _set_current_task_id(task_id)

    try:
        _agent = _ensure_agent()

        result = _agent.run(message)

        with _tasks_lock:
            _tasks[task_id]["status"] = "done"
            _tasks[task_id]["result"] = result
    except ConfirmationDenied:
        # 确认被拒绝，状态已由 confirm_handler 设置
        pass
    except Exception as e:
        with _tasks_lock:
            if task_id in _tasks:
                _tasks[task_id]["status"] = "error"
                _tasks[task_id]["result"] = f"出错了: {e}"


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
  .model-select, .authz-select {
    background: var(--card);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 12px;
    max-width: 240px;
    outline: none;
    cursor: pointer;
  }
  .model-select:disabled, .authz-select:disabled { opacity: 0.6; cursor: not-allowed; }
  .authz-select { max-width: 118px; }
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
	    <select id="model-select" class="model-select" onchange="onModelChange()" title="切换模型（切换后自动测试连通性）">
	      <option value="">模型加载中…</option>
	    </select>
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
	  document.getElementById('send-btn').disabled = loading;
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
	  const text = input.value.trim();
	  if (!text || document.getElementById('send-btn').disabled) return;
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
	    startPolling();
	  } catch (e) {
	    addMessage('网络错误: ' + e.message, 'error');
	    setLoading(false);
	  }
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
	        addMessage(data.result, 'bot');
	        polling = false;
	        currentTaskId = null;
	        setLoading(false);
	        await refreshSessions();
	        return;
	      } else if (data.status === 'error') {
	        addMessage(data.result, 'error');
	        polling = false;
	        currentTaskId = null;
	        setLoading(false);
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
	
	// 顶栏徽章：根据后端 /status 实时刷新（离线/在线、会话数），并把当前模型同步到下拉框
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
  setModelControl(data.model, !offline);
  if (data.authz !== undefined) setAuthzControl(data.authz);
  if (data.session_count !== undefined) {
    document.getElementById('session-badge').textContent = '会话: ' + data.session_count;
  }
  return data;
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

function setModelControl(name, online) {
  const sel = document.getElementById('model-select');
  if (!sel) return;
  if (!online || !name) {
    sel.innerHTML = '';
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '🧠 离线模式（未接入模型）';
    sel.appendChild(o);
    sel.value = '';
    sel.disabled = true;
    return;
  }
  sel.disabled = false;
  const has = Array.from(sel.options).some(function (x) { return x.value === name; });
  if (!has) {
    const o = document.createElement('option');
    o.value = name;
    o.textContent = name;
    sel.appendChild(o);
  }
  sel.value = name;
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

async function loadModels() {
  const sel = document.getElementById('model-select');
  if (!sel) return;
  try {
    const r = await fetch('/api/models');
    const d = await r.json();
    if (!d.online || ((!d.models || !d.models.length) && d.error)) {
      sel.innerHTML = '';
      const o = document.createElement('option');
      o.value = '';
      o.textContent = d.online ? '⚠️ 无法获取模型列表' : '🧠 离线模式（未接入模型）';
      sel.appendChild(o);
      sel.value = '';
      sel.disabled = true;
      if (d.message) showToast(d.message, false);
      return;
    }
    sel.innerHTML = '';
    (d.models || []).forEach(function (m) {
      const o = document.createElement('option');
      o.value = m;
      o.textContent = m;
      sel.appendChild(o);
    });
    if (d.current) sel.value = d.current;
    if (d.message) showToast(d.message, false);
  } catch (e) {
    sel.innerHTML = '';
    const o = document.createElement('option');
    o.value = '';
    o.textContent = '模型列表加载失败';
    sel.appendChild(o);
    sel.value = '';
    sel.disabled = true;
  }
}

async function onModelChange() {
  const sel = document.getElementById('model-select');
  const model = sel.value;
  if (!model) return;
  sel.disabled = true;
  try {
    const r = await fetch('/switch-model', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: model }),
    });
    const d = await r.json();
    showToast(d.message, !!d.ok);
    await loadModels();
    await updateStatus();
  } catch (e) {
    showToast('切换出错：' + e.message, false);
  } finally {
    sel.disabled = false;
  }
}
// 初始化
	fetch('/status').then(r => r.json()).then(data => {
	  document.getElementById('provider-badge').textContent =
	    data.provider === 'deterministic' ? '🧠 离线模式' : '☁️ 在线模式';
	  setModelControl(data.model, data.provider !== 'deterministic');
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
	</script>
</body>
</html>"""


# ============================================================
# Flask 路由
# ============================================================

@app.route("/")
def index():
    return HTML


@app.route("/status")
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
        "tools": len(_agent.api.list_tools_mcp()),
        "session_id": _agent.current_session_id,
        "session_title": current.title if current else "新对话",
        "session_count": len(_agent.list_sessions()),
    })


@app.route("/api/models", methods=["GET"])
def api_models():
    cfg = load_config()
    llm = cfg.get("llm", {})
    online = (
        llm.get("provider", "deterministic") != "deterministic"
        and bool(llm.get("base_url"))
        and bool(llm.get("api_key"))
    )
    current = _agent.model_name if _agent is not None else (llm.get("model") or "")
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
    if _agent is not None and hasattr(_agent.llm, "model"):
        _agent.llm.model = model
        _agent.config.setdefault("llm", {})["model"] = model

    return jsonify({
        "ok": True,
        "model": model,
        "message": f"✅ 切换成功：模型 {model} 已连通，可正常使用。",
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

    # 初始化任务状态
    with _tasks_lock:
        _tasks[task_id] = {"status": "pending", "result": None}

    # 启动后台 Agent 线程
    thread = threading.Thread(
        target=_run_agent_thread,
        args=(task_id, message),
        daemon=True,
    )
    thread.start()

    return jsonify({"task_id": task_id})


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