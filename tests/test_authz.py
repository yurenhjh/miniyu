"""
test_authz.py：授权档位（base / advanced / full）单元测试

覆盖：
- safety.should_confirm：按档位决定哪些 HIGH 操作要确认（base=全部 / advanced=仅永久删除
  / full=从不；非 HIGH 恒不确认）
- safety.resolve_authz_level：agent.authorization 优先，旧 confirm_high_risk=False ⇔ full
- Agent 运行时确认门：advanced 下 run_command/发消息/发邮件/杀进程 自动放行、删除仍确认；
  full 下删除也不确认；旧 confirm_high_risk=False 行为不回归
- agent_config.set_config_authorization：文本级写回（镜像 set_config_model）
- 环境变量 AGENT_AUTHORIZATION 覆盖 load_config

本文件不触碰真 QQ / 邮件 / 真实文件删除：门判定用捕获型 fake API，确认处理用记录回调。
"""

from core import agent_config as agent_config_mod
from core.agent import Agent
from core.agent_config import load_config, set_config_authorization
from core.os_service_api import OSServiceAPI
from core.safety import (
    AUTHZ_ADVANCED,
    AUTHZ_BASE,
    AUTHZ_FULL,
    HIGH,
    LOW,
    MEDIUM,
    READ_ONLY,
    ConfirmationDenied,
    resolve_authz_level,
    should_confirm,
)

# HIGH 全清单（工具 + 白名单技能），与 safety.py 的 TOOL_META / SKILL_META 一致
ALL_HIGH = [
    "delete_file", "delete_directory", "run_command", "kill_process",
    "terminate_process", "cleanup_by_type", "empty_trash",
    "app_send_message", "send_email",
]
# 高级档仍要确认的“永久删除类”
DELETE_SET = {"delete_file", "delete_directory", "cleanup_by_type", "empty_trash"}
# 高级档自动放行的其余 HIGH
AUTO_ADVANCED = ["run_command", "kill_process", "terminate_process",
                 "app_send_message", "send_email"]


# ============================================================
# safety.should_confirm（纯函数）
# ============================================================

class TestShouldConfirm:
    def test_base_confirms_all_high(self):
        for name in ALL_HIGH:
            assert should_confirm(AUTHZ_BASE, name, HIGH) is True

    def test_advanced_only_confirms_delete(self):
        for name in DELETE_SET:
            assert should_confirm(AUTHZ_ADVANCED, name, HIGH) is True

    def test_advanced_auto_allows_other_high(self):
        for name in AUTO_ADVANCED:
            assert should_confirm(AUTHZ_ADVANCED, name, HIGH) is False

    def test_advanced_delete_inventory_is_exact(self):
        confirmed = {n for n in ALL_HIGH if should_confirm(AUTHZ_ADVANCED, n, HIGH)}
        assert confirmed == DELETE_SET

    def test_full_never_confirms(self):
        for name in ALL_HIGH:
            assert should_confirm(AUTHZ_FULL, name, HIGH) is False

    def test_non_high_never_confirms_even_base(self):
        for risk in (READ_ONLY, LOW, MEDIUM):
            assert should_confirm(AUTHZ_BASE, "whatever", risk) is False
            assert should_confirm(AUTHZ_FULL, "whatever", risk) is False

    def test_unknown_level_falls_back_to_base(self):
        # 非法档位按 base 处理（维持现状：全部 HIGH 确认）
        assert should_confirm("ultra", "run_command", HIGH) is True
        assert should_confirm(None, "delete_file", HIGH) is True


# ============================================================
# safety.resolve_authz_level（配置解析）
# ============================================================

class TestResolveAuthzLevel:
    def test_default_is_base(self):
        assert resolve_authz_level({}) == AUTHZ_BASE
        assert resolve_authz_level({"confirm_high_risk": True}) == AUTHZ_BASE
        assert resolve_authz_level(None) == AUTHZ_BASE

    def test_legacy_confirm_high_risk_false_is_full(self):
        assert resolve_authz_level({"confirm_high_risk": False}) == AUTHZ_FULL

    def test_authorization_wins_over_legacy(self):
        assert resolve_authz_level(
            {"confirm_high_risk": False, "authorization": AUTHZ_ADVANCED}) == AUTHZ_ADVANCED
        assert resolve_authz_level(
            {"confirm_high_risk": True, "authorization": AUTHZ_FULL}) == AUTHZ_FULL

    def test_invalid_authorization_falls_back_to_base(self):
        assert resolve_authz_level(
            {"confirm_high_risk": True, "authorization": "ultra"}) == AUTHZ_BASE
        # 非法值 + 旧字段 False：仍回落 full（按旧字段）
        assert resolve_authz_level(
            {"confirm_high_risk": False, "authorization": "ultra"}) == AUTHZ_FULL


# ============================================================
# Agent 运行时确认门（档位决定是否调 confirm_handler）
# ============================================================

class _CaptureAPI(OSServiceAPI):
    """捕获 execute_tool / run_skill，绝不真执行（防止真删文件 / 真外发）"""

    def __init__(self):
        super().__init__()
        self.tool_calls = []
        self.skill_calls = []

    def execute_tool(self, name, params=None):
        self.tool_calls.append((name, params or {}))
        return {"success": True, "tool": name, "result": {"ok": 1}}

    def run_skill(self, name, params=None):
        self.skill_calls.append((name, params or {}))
        return {"success": True, "skill": name, "result": {"ok": 1}}


def _cfg(tmp, agent_extra=None):
    cfg = {
        "llm": {"provider": "deterministic"},
        "agent": {"max_steps": 15, "confirm_high_risk": True, "history_window": 20},
        "memory": {"storage_dir": str(tmp)},
    }
    cfg["agent"].update(agent_extra or {})
    return cfg


def _recording_handler(calls):
    def handler(pv):
        calls.append(pv)
        return True

    return handler


class TestAgentGate:
    def test_base_delete_confirmed_then_denied(self, tmp_path):
        api = _CaptureAPI()
        agent = Agent(config=_cfg(tmp_path), api=api)

        def deny(_pv):
            raise ConfirmationDenied("delete_file")

        agent.confirm_handler = deny
        r = agent._execute_one("delete_file", {"path": "x"})
        assert r["success"] is False
        assert "拒绝" in r["error"]
        assert api.tool_calls == []          # 被拒 → 绝不真正执行

    def test_base_delete_confirmed_then_runs(self, tmp_path):
        calls = []
        api = _CaptureAPI()
        agent = Agent(config=_cfg(tmp_path), api=api)
        agent.confirm_handler = _recording_handler(calls)
        r = agent._execute_one("delete_file", {"path": "x"})
        assert r["success"] is True
        assert len(calls) == 1               # 弹了一次确认
        assert api.tool_calls[0][0] == "delete_file"

    def test_advanced_delete_still_confirmed(self, tmp_path):
        calls = []
        api = _CaptureAPI()
        agent = Agent(config=_cfg(tmp_path, {"authorization": AUTHZ_ADVANCED}), api=api)
        agent.confirm_handler = _recording_handler(calls)
        agent._execute_one("delete_directory", {"path": "x"})
        assert len(calls) == 1               # 删除仍是确认项
        assert api.tool_calls[0][0] == "delete_directory"

    def test_advanced_auto_allows_other_high(self, tmp_path):
        """高级档下 run_command / kill_process / 发消息 / 发邮件 都不再弹确认，直接执行"""
        calls = []
        api = _CaptureAPI()
        agent = Agent(config=_cfg(tmp_path, {"authorization": AUTHZ_ADVANCED}), api=api)
        agent.confirm_handler = _recording_handler(calls)

        r_tool = agent._execute_one("run_command", {"command": "echo hi"})
        r_kill = agent._execute_one("kill_process", {"name": "x"})
        r_skill = agent._execute_one("send_email", {"to": "a@b.c", "subject": "s", "body": "b"})
        r_qq = agent._execute_one("app_send_message",
                                  {"app_name": "QQ", "search_keyword": "群", "message": "hi"})

        assert r_tool["success"] is True
        assert r_kill["success"] is True
        assert r_skill["success"] is True
        assert r_qq["success"] is True
        assert calls == []                   # 全程没弹任何确认
        assert [c[0] for c in api.tool_calls] == ["run_command", "kill_process"]
        assert [c[0] for c in api.skill_calls] == ["send_email", "app_send_message"]

    def test_full_delete_not_confirmed(self, tmp_path):
        calls = []
        api = _CaptureAPI()
        agent = Agent(config=_cfg(tmp_path, {"authorization": AUTHZ_FULL}), api=api)
        agent.confirm_handler = _recording_handler(calls)
        r = agent._execute_one("delete_file", {"path": "x"})
        assert r["success"] is True
        assert calls == []                   # 全自动：删除也放行

    def test_legacy_confirm_false_regression(self, tmp_path):
        """旧配置（只写 confirm_high_risk: false、没有 authorization）行为 == full：删除不确认"""
        calls = []
        api = _CaptureAPI()
        agent = Agent(config=_cfg(tmp_path, {"confirm_high_risk": False}), api=api)
        agent.confirm_handler = _recording_handler(calls)
        r = agent._execute_one("delete_file", {"path": "x"})
        assert r["success"] is True
        assert calls == []

    def test_agent_reports_authz_level(self, tmp_path):
        for level in (AUTHZ_BASE, AUTHZ_ADVANCED, AUTHZ_FULL):
            agent = Agent(config=_cfg(tmp_path, {"authorization": level}))
            assert agent.authz_level == level


# ============================================================
# agent_config.set_config_authorization（文本级写回）
# ============================================================

# 带中文注释、agent 段含旧字段的假 config.yaml（无真实 key）
_SAMPLE_CONFIG = """\
# 第4组 miniyu 测试配置

llm:
  provider: deterministic
  api_key: ""
  model: "qwen3.5-plus"

agent:
  max_steps: 15
  confirm_high_risk: true
  history_window: 20
"""


class TestSetConfigAuthorization:
    def _write(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(_SAMPLE_CONFIG, encoding="utf-8")
        monkeypatch.setattr(agent_config_mod, "_find_config", lambda: cfg_file)
        return cfg_file

    def test_inserts_authorization_line(self, tmp_path, monkeypatch):
        cfg_file = self._write(tmp_path, monkeypatch)
        set_config_authorization(AUTHZ_ADVANCED)
        text = cfg_file.read_text(encoding="utf-8")
        assert 'authorization: "advanced"' in text
        # 其它字段与中文注释保留，旧字段也保留
        assert "confirm_high_risk: true" in text
        assert "max_steps: 15" in text
        assert "第4组 miniyu 测试配置" in text

    def test_roundtrip_via_load_config(self, tmp_path, monkeypatch):
        cfg_file = self._write(tmp_path, monkeypatch)
        set_config_authorization(AUTHZ_ADVANCED)
        cfg = load_config()
        assert cfg["agent"]["authorization"] == AUTHZ_ADVANCED
        # 再切回 full：改行而非再插一行
        set_config_authorization(AUTHZ_FULL)
        text = cfg_file.read_text(encoding="utf-8")
        assert text.count("authorization:") == 1
        assert load_config()["agent"]["authorization"] == AUTHZ_FULL

    def test_env_authorization_overrides_config(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch)
        monkeypatch.setenv("AGENT_AUTHORIZATION", AUTHZ_FULL)
        cfg = load_config()
        assert cfg["agent"]["authorization"] == AUTHZ_FULL


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
