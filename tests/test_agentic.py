"""
test_agentic.py
第4组：安全分级 + 确认门 + 执行引擎 + 任务分类 测试
"""

import unittest

from core import safety, classifier
from core.tool_registry import ToolRegistry
from core.skill_library import SkillLibrary
from core.executor import Plan, ExecutionEngine
from core.os_service_api import OSServiceAPI


class _FakeAPI:
    """记录调用、返回固定结果的最小联调替身"""

    def __init__(self):
        self.calls = []

    def execute_tool(self, name, params):
        self.calls.append(("tool", name, params))
        return {"success": True, "tool": name, "result": {"name": name, "params": params}}

    def run_skill(self, name, params):
        self.calls.append(("skill", name, params))
        return {"success": True, "skill": name, "result": {"name": name, "params": params}}


class TestSafetyMeta(unittest.TestCase):
    """风险分级元数据"""

    def test_risk_order(self):
        self.assertEqual(safety._RISK_ORDER[safety.READ_ONLY], 0)
        self.assertEqual(safety._RISK_ORDER[safety.HIGH], 3)

    def test_requires_confirmation_only_high(self):
        self.assertTrue(safety.requires_confirmation(safety.HIGH))
        self.assertFalse(safety.requires_confirmation(safety.MEDIUM))
        self.assertFalse(safety.requires_confirmation(safety.LOW))

    def test_tool_meta(self):
        reg = ToolRegistry()
        self.assertEqual(reg.get_tool_meta("delete_file")["risk"], safety.HIGH)
        self.assertEqual(reg.get_tool_meta("copy_file")["risk"], safety.LOW)
        self.assertEqual(reg.get_tool_meta("list_directory")["risk"], safety.READ_ONLY)

    def test_skill_meta(self):
        sk = SkillLibrary()
        self.assertEqual(sk.get_skill_meta("app_send_message")["risk"], safety.HIGH)
        self.assertEqual(sk.get_skill_meta("system_info")["risk"], safety.READ_ONLY)

    def test_list_meta_covers_all(self):
        reg = ToolRegistry()
        self.assertEqual(len(reg.list_tools_meta()), len(reg.list_tools()))
        sk = SkillLibrary()
        self.assertEqual(len(sk.list_skills_meta()), len(sk.list_skills()))


class TestCallSafely(unittest.TestCase):
    """带确认门的工具/技能调用"""

    def test_deny_blocks_high_risk(self):
        reg = ToolRegistry()
        r = reg.call_safely("delete_file", {"path": "x.txt"}, confirm_handler=lambda pv: False)
        self.assertFalse(r["success"])
        self.assertEqual(r["error_code"], "CONFIRMATION_DENIED")

    def test_allow_proceeds_to_execution(self):
        reg = ToolRegistry()
        # 放行后真正执行删除，但文件不存在 -> TOOL_ERROR，证明已越过确认门
        r = reg.call_safely("delete_file", {"path": "x.txt"}, confirm_handler=lambda pv: True)
        self.assertFalse(r["success"])
        self.assertEqual(r["error_code"], "TOOL_ERROR")

    def test_low_risk_skips_gate(self):
        reg = ToolRegistry()
        r = reg.call_safely("current_directory", {})
        self.assertTrue(r["success"])

    def test_skill_safely_deny(self):
        sk = SkillLibrary()
        r = sk.run_skill_safely("app_send_message", {"app_name": "QQ", "message": "hi"},
                                confirm_handler=lambda pv: False)
        self.assertFalse(r["success"])
        self.assertEqual(r["error_code"], "CONFIRMATION_DENIED")


class TestExecutor(unittest.TestCase):
    """多步执行引擎"""

    def test_runs_all_steps(self):
        api = _FakeAPI()
        eng = ExecutionEngine(api)
        plan = Plan().add("s1", "tool", "a", {"x": 1}).add("s2", "skill", "b", {})
        r = eng.execute(plan)
        self.assertTrue(r["success"])
        self.assertEqual(len(r["results"]), 2)
        self.assertEqual(api.calls, [("tool", "a", {"x": 1}), ("skill", "b", {})])

    def test_ref_resolution(self):
        api = _FakeAPI()
        eng = ExecutionEngine(api)
        plan = Plan().add("s1", "tool", "probe", {}).add(
            "s2", "tool", "use", {"from_prev": "$ref:s1.result.name"})
        eng.execute(plan)
        self.assertEqual(api.calls[1][2], {"from_prev": "probe"})

    def test_confirm_step_denies_and_stops(self):
        api = _FakeAPI()
        eng = ExecutionEngine(api)
        seen = []

        def deny(pv):
            seen.append(pv)
            return False

        plan = (Plan()
                .add("s1", "tool", "prep", {})
                .add("s2", "tool", "send", {}, describe="发送消息", confirm=True))
        r = eng.execute(plan, confirm_handler=deny)
        self.assertFalse(r["success"])
        self.assertEqual(r["error_code"], "CONFIRMATION_DENIED")
        self.assertEqual([c[0] for c in api.calls], ["tool"])  # 只执行了第一步
        self.assertEqual(seen[0]["name"], "send")

    def test_high_risk_step_denies_without_handler(self):
        api = _FakeAPI()
        eng = ExecutionEngine(api)
        plan = Plan().add("s1", "tool", "delete_file", {})
        r = eng.execute(plan)  # 无处理器 + high 风险 -> fail-safe 拒绝
        self.assertFalse(r["success"])
        self.assertEqual(r["error_code"], "CONFIRMATION_DENIED")
        self.assertEqual(api.calls, [])

    def test_confirm_allowed_continues(self):
        api = _FakeAPI()
        eng = ExecutionEngine(api)
        plan = Plan().add("s1", "tool", "delete_file", {})
        r = eng.execute(plan, confirm_handler=lambda pv: True)
        self.assertTrue(r["success"])

    def test_warning_gate(self):
        api = _FakeAPI()
        eng = ExecutionEngine(api)
        plan = Plan().add("s1", "tool", "x", {})
        # 拒绝 -> 不执行
        r = eng.execute(plan, warning="可能做不好", confirm_handler=lambda pv: False)
        self.assertFalse(r["success"])
        self.assertEqual(r["error_code"], "CONFIRMATION_DENIED")
        self.assertEqual(api.calls, [])
        # 许可 -> 尽力执行
        r2 = eng.execute(Plan().add("s1", "tool", "x", {}),
                         warning="可能做不好", confirm_handler=lambda pv: True)
        self.assertTrue(r2["success"])


class TestClassifier(unittest.TestCase):
    """任务分类器"""

    def test_auto(self):
        d = classifier.classify("整理一下下载文件夹")
        self.assertEqual(d["decision"], "auto")

    def test_confirm(self):
        d = classifier.classify("删除所有临时文件")
        self.assertEqual(d["decision"], "confirm")
        self.assertTrue(d["high_risk"])

    def test_warn_creative(self):
        d = classifier.classify("用 blender 建一个 3D 模型")
        self.assertEqual(d["decision"], "warn")
        self.assertTrue(d["creative"])
        self.assertIsNotNone(d["hint"])


class TestOSServiceAPIIntegration(unittest.TestCase):
    """统一接口暴露新能力"""

    def test_meta_classify_confirm(self):
        api = OSServiceAPI()
        self.assertIn("delete_file", api.list_tools_meta())
        self.assertEqual(api.classify_task("画一张海报")["decision"], "warn")
        api.set_confirm_handler(lambda pv: True)
        self.assertEqual(api.get_tool_meta("run_command")["risk"], safety.HIGH)


if __name__ == "__main__":
    unittest.main()