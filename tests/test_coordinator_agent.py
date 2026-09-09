"""
test_coordinator_agent.py
第5组系统协调层 × Agent 集成：验证 run/run_stream 结束自动记审计 + RAG 轨迹
（使用 DeterministicBrain 离线脑，不依赖真实 LLM）
"""
import os
import tempfile

import pytest

from core.agent import Agent
from core.llm_client import DeterministicBrain, create_llm_client


def _make_agent():
    cfg = {
        "llm": {"provider": "deterministic"},
        "agent": {"coordinator": {"enabled": True}},
        "memory": {"storage_dir": tempfile.mkdtemp(prefix="miniyu_test_")},
    }
    agent = Agent(config=cfg, llm_client=DeterministicBrain())
    assert agent.coordinator is not None
    return agent


def test_agent_run_records_audit_and_rag():
    agent = _make_agent()
    agent.run("帮我整理下载目录")
    # 审计至少一条 agent_turn
    assert any(e["event"] == "agent_turn" for e in agent.coordinator.audit.query())
    # RAG 至少一条轨迹，且能检索到
    assert agent.coordinator.rag.count() >= 1
    hits = agent.coordinator.rag.query("整理下载")
    assert hits


def test_agent_run_stream_records_audit_and_rag():
    agent = _make_agent()
    for chunk in agent.run_stream("磁盘空间"):
        pass
    assert any(e["event"] == "agent_turn" for e in agent.coordinator.audit.query())
    assert agent.coordinator.rag.count() >= 1


def test_coordinator_can_be_disabled():
    cfg = {
        "llm": {"provider": "deterministic"},
        "agent": {"coordinator": {"enabled": False}},
        "memory": {"storage_dir": tempfile.mkdtemp(prefix="miniyu_test_")},
    }
    agent = Agent(config=cfg, llm_client=DeterministicBrain())
    assert agent.coordinator is None
    agent.run("你好")
    # 不报错即可（无协调器）
