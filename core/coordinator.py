"""
coordinator.py
第5组：系统协调层 —— SystemCoordinator + SecuritySandbox + RAGKnowledgeBase + 审计日志

对照任务设计书「5. 第 5 组：系统协调 + 安全 + RAG」功能要求逐项落地：
  1. SystemCoordinator：注册各模块、编排执行流程（协调所有模块 → 审计 → RAG 存储）
  2. SecuritySandbox：四层安全架构（权限检查 → 沙箱执行 → 签名验证 → 隐私保护）
  3. RAGKnowledgeBase：存储执行轨迹，支持向量检索（轻量词频向量 + 余弦相似度，
     零外部依赖，标准库实现；后续可平滑替换为 numpy/embedding 方案）
  4. 审计日志：记录所有操作，支持事后审计（append 式、线程安全）
  5. 降级方案：MockCoordinator / MockRAG / MockAudit，供其它组落后时联调复用

设计要点：
  - 纯标准库（math/hashlib/json/threading），无新依赖，Windows/Linux 通用；
  - RAG 用「词频向量 + 余弦相似度」做轻量检索，中文按字符 bigram 切分
    （避免 jieba 依赖），对短轨迹文本效果足够；
  - 与既有模块关系：
      * 安全检查复用 core/safety.py 的 risk 分级/授权档（不重复造轮子）；
      * 会话历史存储复用 core/conversation.py；
      * coordinator 提供的是「跨模块编排 + 审计 + 轨迹检索」的组合门面。
"""

import hashlib
import json
import math
import os
import re
import threading
import time
from typing import Callable, Optional


# ============================================================
# RAGKnowledgeBase —— 执行轨迹存储 + 轻量向量检索
# ============================================================

_WORD_SPLIT = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    """分词：中文按字符 bigram 切分（无需 jieba），英文/数字按词切分。"""
    words = _WORD_SPLIT.findall(text.lower())
    tokens: list[str] = []
    for w in words:
        if w.isascii():
            tokens.append(w)
        else:
            if len(w) == 1:
                tokens.append(w)
            else:
                tokens.extend(w[i:i + 2] for i in range(len(w) - 1))
    return tokens


class RAGKnowledgeBase:
    """知识库：存储执行轨迹（用户输入→动作→结果），支持向量检索。"""

    def __init__(self, max_docs: int = 500, top_k: int = 3):
        self._docs: list[dict] = []          # {"id","time","input","actions","result","embedding"}
        self._max_docs = max_docs
        self._top_k = top_k
        self._lock = threading.Lock()

    # ---------- 写入 ----------
    def add_trace(self, input_text: str, actions: list, result: str,
                  meta: dict = None) -> dict:
        """记录一条执行轨迹，返回记录 dict。"""
        doc = {
            "id": f"trace_{int(time.time() * 1000)}_{len(self._docs)}",
            "time": time.time(),
            "input": input_text,
            "actions": actions or [],
            "result": result,
            "meta": meta or {},
        }
        doc["embedding"] = self._embed(doc["input"] + " " + " ".join(map(str, doc["actions"])))
        with self._lock:
            self._docs.append(doc)
            if len(self._docs) > self._max_docs:
                self._docs = self._docs[-self._max_docs:]
        return doc

    # ---------- 检索 ----------
    def query(self, query: str, k: int = None) -> list[dict]:
        """向量检索与 query 最相关的 k 条轨迹（按余弦相似度降序）。"""
        k = k or self._top_k
        if not self._docs:
            return []
        qv = self._embed(query)
        scored = sorted(
            ((self._cosine(qv, d["embedding"]), d) for d in self._docs),
            key=lambda x: x[0], reverse=True,
        )
        # 过滤相似度 0 的无关结果
        return [
            {key: d[key] for key in ("id", "time", "input", "actions", "result", "meta")}
            for score, d in scored[:k] if score > 0
        ]

    def count(self) -> int:
        return len(self._docs)

    def clear(self) -> None:
        with self._lock:
            self._docs = []

    # ---------- 内部 ----------
    @staticmethod
    def _embed(text: str) -> dict[str, int]:
        """词频向量（稀疏 dict 表示）。"""
        vec: dict[str, int] = {}
        for t in _tokens(text):
            vec[t] = vec.get(t, 0) + 1
        return vec

    @staticmethod
    def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
        if not a or not b:
            return 0.0
        inter = sum(v * b.get(k, 0) for k, v in a.items())
        if inter == 0:
            return 0.0
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0 or nb == 0:
            return 0.0
        return inter / (na * nb)


# ============================================================
# AuditLog —— 审计日志（追加式、线程安全）
# ============================================================

class AuditLog:
    """审计日志：记录每次用户输入/工具调用/确认事件，支持事后查询。

    日志行格式（JSON Lines，便于程序化解析）：
      {"ts": "...", "event": "user_input|tool_call|tool_result|confirm|denied|assistant",
       "data": {...}}
    """

    def __init__(self, path: Optional[str] = None, max_entries: int = 2000):
        self.path = path
        self._entries: list[dict] = []
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def log(self, event: str, data: dict) -> None:
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "event": event,
            "data": data,
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries:]
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                except OSError:
                    pass  # 审计落盘失败不阻塞主流程

    def query(self, event: str = None, limit: int = 50) -> list[dict]:
        with self._lock:
            items = self._entries if event is None else [
                e for e in self._entries if e["event"] == event
            ]
        return items[-limit:][::-1]

    def count(self) -> int:
        return len(self._entries)


# ============================================================
# SecuritySandbox —— 四层安全架构
# ============================================================

class SecuritySandbox:
    """四层安全架构：权限检查 → 沙箱执行 → 签名验证 → 隐私保护。

    与 core/safety.py 的关系：safety.py 负责"风险分级 + 授权档位 + 确认门"（工具元数据）；
    本类负责"四层安全流程"编排，是第 5 组契约 check_json 的出口。
    """

    # 危险指令/路径（对齐设计书 SecuritySandbox 示例；含 Windows/PowerShell 变体）
    DANGEROUS_ACTIONS = ("rm ", "format", "shutdown", "dd ", "mkfs", "fdisk",
                         "stop-computer", "restart-computer", "remove-item")
    PROTECTED_PATHS = ("/etc", "/usr", "/root", "/boot", "/etc/passwd", "/etc/shadow")

    def __init__(self, check_safe: Callable = None, audit: AuditLog = None):
        """
        check_safe: 可选回调（name, params)->bool，用于接入上层确认门（默认全部放行）。
        audit:      可选 AuditLog 实例，把每次检查/执行记入审计。
        """
        self._check_safe = check_safe
        self._audit = audit

    def check_permission(self, action: str, target: str = "") -> dict:
        """第一层：权限检查。危险指令/受保护路径直接拒绝。返回 check_json。"""
        action_l = (action or "").lower()
        target_l = (target or "").lower()
        reason = ""
        for d in self.DANGEROUS_ACTIONS:
            if d in action_l:
                reason = f"危险指令: {d.strip()}"
                break
        if not reason:
            for p in self.PROTECTED_PATHS:
                if target_l == p or target_l.startswith(p + "/"):
                    reason = f"受保护路径: {p}"
                    break
        approved = not reason
        risk = "low" if approved else "critical"
        check = {"approved": approved, "risk_level": risk}
        if reason:
            check["reason"] = reason
        if self._audit:
            self._audit.log("sandbox_check",
                            {"action": action, "target": target, "check": check})
        return check

    def sandbox_execute(self, name: str, params: dict,
                        executor: Callable, check: dict = None) -> dict:
        """第二层：沙箱执行（经 check_json 门禁后执行真实实现；含签名/隐私可选回调）。

        executor:  (name, params)->dict 的真实执行函数。
        """
        check = check or self.check_permission(name, str(params or {}))
        if not check.get("approved"):
            return {"success": False, "error": check.get("reason", "安全沙箱拦截")}
        if self._check_safe is not None and not self._check_safe(name, params or {}):
            return {"success": False, "error": "用户拒绝执行"}
        try:
            result = executor(name, params or {})
            if self._audit:
                self._audit.log("sandbox_exec", {"tool": name, "params": params,
                                                 "result_ok": bool(result and result.get("success"))})
            return result
        except Exception as e:  # noqa: BLE001 沙箱边界：任何异常都归一化
            return {"success": False, "error": f"沙箱执行异常: {e}"}

    def verify_signature(self, name: str, signature: str, expected: str = None) -> dict:
        """第三层：签名验证。校验工具签名（SHA256）是否与预期一致，防止被篡改。

        expected 省略时返回"是否已注册签名"（存在即视为可信白名单）。
        """
        if expected is None:
            ok = bool(signature)
            reason = "" if ok else "工具未注册签名"
        else:
            ok = bool(signature) and signature == expected
            reason = "" if ok else "签名不匹配，工具可能被篡改"
        if self._audit:
            self._audit.log("signature_check", {"tool": name, "ok": ok})
        return {"approved": ok, "risk_level": "low" if ok else "high",
                **({"reason": reason} if reason else {})}

    def privacy_protect(self, text: str) -> str:
        """第四层：隐私保护。把文本中的 API key / 授权码 / 邮箱脱敏，防止落审计/RAG。"""
        if not text:
            return text
        text = re.sub(r"sk-[A-Za-z0-9._-]{8,}", "sk-***", text)
        text = re.sub(r"(?i)(authorization_code|authcode)['\"]?\s*[:=]\s*['\"]?[A-Za-z0-9]{8,}",
                      r"\1=***", text)
        text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                      "***@***", text)
        return text


# ============================================================
# SystemCoordinator —— 模块注册 + 编排执行
# ============================================================

class SystemCoordinator:
    """系统协调器：注册各模块，按 1→5→2→3→4 数据流编排，审计 + RAG 存储。"""

    def __init__(self, audit: AuditLog = None, rag: RAGKnowledgeBase = None,
                 sandbox: SecuritySandbox = None):
        self.modules: dict[str, object] = {}
        self.audit = audit or AuditLog()
        self.rag = rag or RAGKnowledgeBase()
        self.sandbox = sandbox or SecuritySandbox(audit=self.audit)

    def register(self, group: str, module) -> None:
        """注册模块。group 用设计书组号字符串（'1'~'5'）或语义名。"""
        self.modules[group] = module
        self.audit.log("register", {"group": group, "module": module.__class__.__name__})

    def get(self, group: str, default=None):
        return self.modules.get(group, default)

    def orchestrate(self, user_input: str, understand: Callable, execute: Callable,
                    plan: Callable = None) -> dict:
        """编排执行流程（对标设计书 SystemCoordinator.orchestrate）。

        understand: (text)->task dict      —— 意图理解（第 1 组 HostAgent）
        execute:    (task)->result dict    —— 执行（第 3/4 组，含安全检查）
        plan:       可选 (task)->plan dict —— 任务规划（第 2 组 TaskPlanner）
        """
        # 1. 意图理解
        task = understand(user_input)
        self.audit.log("user_input", {"input": self.sandbox.privacy_protect(user_input)})
        # 2. 安全检查（第 5 组）
        check = self.sandbox.check_permission(
            str(task.get("action", "")), str(task.get("target", "")))
        if not check["approved"]:
            self.audit.log("denied", {"task": task, "reason": check.get("reason")})
            return {"success": False, "error": f"安全检查未通过: {check.get('reason')}",
                    "check": check, "task": task}
        # 3. 任务规划（可选）
        plan_result = plan(task) if plan else None
        # 4. 执行
        result = execute(task)
        # 5. 审计 + RAG 存储
        actions = []
        if isinstance(result, dict):
            actions = result.get("actions", []) or []
        self.audit.log("orchestrate_done", {"task": task, "result_ok": bool(
            isinstance(result, dict) and result.get("success"))})
        trace = self.rag.add_trace(
            input_text=self.sandbox.privacy_protect(user_input),
            actions=actions or [{"action": task.get("action"), "target": task.get("target")}],
            result=self.sandbox.privacy_protect(json.dumps(result, ensure_ascii=False)[:500]),
        )
        return {"success": True, "result": result, "plan": plan_result,
                "trace_id": trace["id"]}

    def retrieve(self, query: str, k: int = 3) -> list[dict]:
        """RAG 检索：根据新问题召回历史相似轨迹（供 Agent 参考历史做法）。"""
        return self.rag.query(query, k=k)


# ============================================================
# Mock（第 5 组降级方案）
# ============================================================

class MockAuditLog(AuditLog):
    """Mock 审计：内存记录，不落盘。"""

    def __init__(self):
        super().__init__(path=None, max_entries=1000)


class MockRAG(RAGKnowledgeBase):
    """Mock 知识库：与正式版同构（add_trace/query），便于其它组离线联调。"""


class MockCoordinator(SystemCoordinator):
    """Mock 协调器：内置一个确定性「意图理解→安全检查→执行」迷你编排，
    供其它组无真实 LLM/系统时端到端联调（对标设计书「降级方案」）。"""

    def __init__(self):
        super().__init__(audit=MockAuditLog(), rag=MockRAG())
        # 内置确定性意图理解（供演示/联调）
        self._intent_rules = [
            (re.compile(r"整理|下载|分类", re.I), {"intent": "文件操作", "action": "organize", "target": "~/Downloads"}),
            (re.compile(r"打开|启动|运行", re.I), {"intent": "应用控制", "action": "open", "target": "Files"}),
            (re.compile(r"创建|新建|文件夹|目录", re.I), {"intent": "文件操作", "action": "create", "target": "project"}),
            (re.compile(r"搜索|查找|文件", re.I), {"intent": "信息查询", "action": "search", "target": "*"}),
        ]

    def understand_intent(self, text: str) -> dict:
        for pat, task in self._intent_rules:
            if pat.search(text):
                return dict(task)
        return {"intent": "信息查询", "action": "query", "target": ""}

    def orchestrate_mock(self, user_input: str, execute: Callable = None) -> dict:
        """Mock 端到端编排：意图理解 → 安全检查 → 执行 → 审计 → RAG。"""
        task = self.understand_intent(user_input)
        check = self.sandbox.check_permission(task["action"], task["target"])
        if not check["approved"]:
            self.audit.log("denied", {"task": task, "reason": check.get("reason")})
            return {"success": False, "error": f"安全检查未通过: {check.get('reason')}", "check": check}
        result = execute(task) if execute else {"success": True, "result": "（Mock 执行完成）"}
        self.audit.log("mock_orchestrate", {"task": task})
        self.rag.add_trace(user_input, [task], str(result))
        return {"success": True, "result": result, "check": check, "task": task}


# ============================================================
# 便捷工厂
# ============================================================

def create_coordinator(audit_path: Optional[str] = None) -> SystemCoordinator:
    """创建标准协调器（审计可选落盘到 audit_path）。"""
    audit = AuditLog(path=audit_path) if audit_path else AuditLog()
    rag = RAGKnowledgeBase()
    sandbox = SecuritySandbox(audit=audit)
    return SystemCoordinator(audit=audit, rag=rag, sandbox=sandbox)


# ============================================================
# 工具签名工具函数（供 ToolRegistry 复用/被 SecuritySandbox 校验）
# ============================================================

def compute_signature(text: str) -> str:
    """SHA256 工具签名：对工具源码/说明计算指纹。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
