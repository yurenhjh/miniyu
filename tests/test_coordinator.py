"""
test_coordinator.py
第5组：系统协调层（SystemCoordinator + SecuritySandbox + RAG + 审计）单元测试
"""
import os
import tempfile

import pytest

from core.coordinator import (
    AuditLog, MockAuditLog, MockCoordinator, MockRAG, RAGKnowledgeBase,
    SecuritySandbox, SystemCoordinator, compute_signature, create_coordinator,
)


# ============================================================
# RAGKnowledgeBase
# ============================================================

class TestRAGKnowledgeBase:
    def test_add_and_query_returns_related_trace(self):
        rag = RAGKnowledgeBase()
        rag.add_trace("整理下载目录", ["organize_downloads"], "分类完成 12 个文件")
        rag.add_trace("打开文件管理器", ["app_open"], "已打开 Files")
        hits = rag.query("整理下载")
        assert hits, "应能检索到相关轨迹"
        assert "organize_downloads" in hits[0]["actions"][0]

    def test_query_empty_returns_empty(self):
        assert RAGKnowledgeBase().query("任意") == []

    def test_query_ranks_relevant_first(self):
        rag = RAGKnowledgeBase()
        rag.add_trace("发送邮件给张三", ["send_email"], "已发送")
        rag.add_trace("整理下载目录", ["organize_downloads"], "完成")
        hits = rag.query("发送邮件")
        assert "send_email" in hits[0]["actions"][0]

    def test_count_and_clear(self):
        rag = RAGKnowledgeBase()
        rag.add_trace("a", ["t1"], "r")
        rag.add_trace("b", ["t2"], "r")
        assert rag.count() == 2
        rag.clear()
        assert rag.count() == 0


# ============================================================
# AuditLog
# ============================================================

class TestAuditLog:
    def test_log_and_query(self):
        audit = AuditLog()
        audit.log("user_input", {"text": "hi"})
        audit.log("tool_call", {"tool": "copy_file"})
        assert audit.count() == 2
        tools = audit.query(event="tool_call")
        assert len(tools) == 1 and tools[0]["data"]["tool"] == "copy_file"

    def test_persist_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "audit.jsonl")
            audit = AuditLog(path=path)
            audit.log("user_input", {"text": "你好"})
            assert os.path.exists(path)
            content = open(path, encoding="utf-8").read()
            assert "user_input" in content

    def test_max_entries_bounded(self):
        audit = AuditLog(max_entries=3)
        for i in range(10):
            audit.log("evt", {"i": i})
        assert audit.count() == 3
        assert audit.query()[0]["data"]["i"] == 9


# ============================================================
# SecuritySandbox
# ============================================================

class TestSecuritySandbox:
    def test_deny_dangerous_action(self):
        sb = SecuritySandbox()
        check = sb.check_permission("rm -rf /tmp/x", "")
        assert check["approved"] is False
        assert check["risk_level"] == "critical"

    def test_deny_protected_path(self):
        sb = SecuritySandbox()
        check = sb.check_permission("write_file", "/etc/passwd")
        assert check["approved"] is False

    def test_allow_safe_action(self):
        sb = SecuritySandbox()
        check = sb.check_permission("copy_file", "/home/user/a.txt")
        assert check["approved"] is True
        assert check["risk_level"] == "low"

    def test_sandbox_execute_passes_check(self):
        sb = SecuritySandbox()
        result = sb.sandbox_execute("copy_file", {"src": "a"},
                                    executor=lambda n, p: {"success": True, "result": "ok"})
        assert result["success"] is True

    def test_sandbox_execute_denied_when_check_fails(self):
        sb = SecuritySandbox()
        result = sb.sandbox_execute("rm -rf /", {"target": "/"},
                                    executor=lambda n, p: {"success": True})
        assert result["success"] is False
        assert "危险指令" in result["error"] or "安全沙箱拦截" in result["error"]

    def test_sandbox_execute_user_deny(self):
        sb = SecuritySandbox(check_safe=lambda n, p: False)
        result = sb.sandbox_execute("copy_file", {}, executor=lambda n, p: {"success": True})
        assert result["success"] is False
        assert "用户拒绝" in result["error"]

    def test_sandbox_execute_normalizes_exception(self):
        sb = SecuritySandbox()
        result = sb.sandbox_execute("boom", {},
                                    executor=lambda n, p: (_ for _ in ()).throw(RuntimeError("x")))
        assert result["success"] is False
        assert "沙箱执行异常" in result["error"]

    def test_verify_signature_match_and_mismatch(self):
        sb = SecuritySandbox()
        sig = compute_signature("def tool(): pass")
        assert sb.verify_signature("t", sig, expected=sig)["approved"] is True
        assert sb.verify_signature("t", sig, expected="deadbeef")["approved"] is False

    def test_privacy_protect_masks_secrets(self):
        sb = SecuritySandbox()
        out = sb.privacy_protect("key=sk-abcdefghijklmnop, email=a@b.com")
        assert "sk-***" in out
        assert "abcdefghijklmnop" not in out
        assert "a@b.com" not in out


# ============================================================
# SystemCoordinator
# ============================================================

class TestSystemCoordinator:
    def test_orchestrate_success_flow(self):
        coord = create_coordinator()
        result = coord.orchestrate(
            user_input="整理下载目录",
            understand=lambda t: {"intent": "文件操作", "action": "organize", "target": "~/Downloads"},
            execute=lambda task: {"success": True, "actions": ["organize_downloads"], "result": "完成"},
        )
        assert result["success"] is True
        assert result["trace_id"]
        # 审计与 RAG 都落了一条
        assert coord.audit.count() >= 3
        assert coord.rag.count() == 1

    def test_orchestrate_denied_on_dangerous(self):
        coord = create_coordinator()
        result = coord.orchestrate(
            user_input="删除系统文件",
            understand=lambda t: {"intent": "文件操作", "action": "rm", "target": "/etc"},
            execute=lambda task: {"success": True},
        )
        assert result["success"] is False
        assert "安全检查未通过" in result["error"]

    def test_register_and_get(self):
        coord = create_coordinator()
        coord.register("1", object())
        assert coord.get("1") is not None

    def test_retrieve_from_rag(self):
        coord = create_coordinator()
        coord.orchestrate(
            user_input="整理下载目录",
            understand=lambda t: {"intent": "文件操作", "action": "organize", "target": "x"},
            execute=lambda task: {"success": True, "actions": ["organize_downloads"], "result": "完成"},
        )
        hits = coord.retrieve("整理下载")
        assert hits and "organize_downloads" in hits[0]["actions"][0]


# ============================================================
# Mock（第 5 组降级方案）
# ============================================================

class TestMockCoordinator:
    def test_understand_intent_rules(self):
        m = MockCoordinator()
        assert m.understand_intent("帮我整理下载目录")["action"] == "organize"
        assert m.understand_intent("打开文件管理器")["action"] == "open"
        assert m.understand_intent("创建project文件夹")["action"] == "create"

    def test_orchestrate_mock_success(self):
        m = MockCoordinator()
        result = m.orchestrate_mock("整理下载目录")
        assert result["success"] is True
        assert m.rag.count() == 1
        assert m.audit.count() >= 2

    def test_mock_denies_dangerous(self):
        m = MockCoordinator()
        # 直接调沙箱，验证 mock 也过安全门
        check = m.sandbox.check_permission("rm -rf /", "")
        assert check["approved"] is False

    def test_mock_rag_is_same_interface(self):
        m = MockCoordinator()
        m.rag.add_trace("打开文件", ["app_open"], "ok")
        assert isinstance(m.rag, MockRAG)
        assert m.rag.query("打开文件")
