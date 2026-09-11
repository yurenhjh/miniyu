"""
test_agent_skills.py
第4组：Agent 白名单组合技能 —— “模型可调函数”接线 + 发送类 OCR 默认门

背景（为什么有这层）：
    Agent 原本只把 ToolRegistry 的 59 个底层工具暴露给 LLM（list_tools_openai），
    昨天做好的“QQ 搜索+发送”是 SkillLibrary 技能 app_send_message，模型看不见也调不到，
    只能退化成激活窗口+输字的零散原语、可能发错会话。本测试锁定“技能被当成一个
    函数暴露给模型、经 _execute_one 走技能分发、且发送默认带 OCR 门”这条新链路。

本文件不触碰真 QQ / 真实视觉桥：技能分发用捕获型 fake API，OCR 门用 patch 拦截
    node 调用与截图裁剪，仅验证接线与门逻辑。
"""

import os
import tempfile
import types
import unittest
from unittest.mock import patch

from core import skill_library as skill_mod
from core.agent import Agent
from core.os_service_api import OSServiceAPI
from core.safety import ConfirmationDenied
from core.skill_library import SkillLibrary

RECT = {"left": 100, "top": 100, "right": 900, "bottom": 700,
        "width": 800, "height": 600}


def _cfg(tmp, confirm=True):
    return {
        "llm": {"provider": "deterministic"},
        "agent": {"max_steps": 15, "confirm_high_risk": confirm, "history_window": 20},
        "memory": {"storage_dir": tmp},
    }


class _CaptureAPI(OSServiceAPI):
    """捕获 run_skill / execute_tool 调用，绝不真执行（防止误发真实 QQ）"""

    def __init__(self):
        super().__init__()
        self.skill_calls = []
        self.tool_calls = []

    def run_skill(self, name, params=None):
        self.skill_calls.append((name, params or {}))
        return {"success": True, "skill": name, "result": {"ok": 1}}

    def execute_tool(self, name, params=None):
        self.tool_calls.append((name, params or {}))
        return {"success": True, "tool": name, "result": {"ok": 1}}


class _FakeApp:
    """仅实现 app_send_message 走到“构造默认 OCR 门”前所需的原子能力"""

    def __init__(self, rect=RECT):
        self._rect = rect

    def find_window(self, process=None, title=None):
        return {"hwnd": 101, "title": process or title or "QQ", "pid": 1}

    def activate_window(self, hwnd):
        return True

    def get_window_rect(self, hwnd):
        return self._rect


class TestAgentSkillExposure(unittest.TestCase):
    """白名单技能 → 模型可见的 OpenAI function"""

    def test_whitelist_agent_skills(self):
        sk = SkillLibrary()
        self.assertEqual(
            sk.openai_skill_names(),
            ["app_send_message", "read_qq_chat", "send_email",
             "browser_search", "browser_extract"])
        for name in ("app_send_message", "read_qq_chat", "send_email",
                     "browser_search", "browser_extract"):
            self.assertTrue(sk.is_agent_skill(name))
        self.assertFalse(sk.is_agent_skill("system_info"))   # 非白名单不暴露
        self.assertFalse(sk.is_agent_skill("no_such_skill"))

    def test_openai_tool_schema_shape(self):
        sk = SkillLibrary()
        tools = sk.list_openai_tools()
        self.assertEqual(len(tools), 5)
        by_name = {t["function"]["name"]: t["function"] for t in tools}

        fn = by_name["app_send_message"]
        self.assertIn("发送", fn["description"])
        params = fn["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertEqual(params["required"], ["search_keyword", "message"])
        self.assertIn("verify_ocr", params["properties"])
        # 不能把 Python 闭包 verify 暴露给模型（JSON tool_call 传不了）
        self.assertNotIn("verify", params["properties"])

        fn = by_name["send_email"]
        self.assertIn("SMTP", fn["description"])
        params = fn["parameters"]
        self.assertEqual(params["required"], ["to", "subject", "body"])
        self.assertIn("to", params["properties"])
        # 授权码绝不该进模型可见的参数（从 config 读）
        self.assertNotIn("auth_code", params["properties"])
        # verify 是 bool（可 JSON 序列化）→ 可暴露；默认 True（别关回读核验）
        self.assertIn("verify", params["properties"])
        self.assertIs(params["properties"]["verify"].get("default"), True)

    def test_read_qq_chat_schema_is_read_only(self):
        """read_qq_chat：只读技能 → 无 message 参数、OCR 默认开、不要求确认"""
        sk = SkillLibrary()
        by_name = {t["function"]["name"]: t["function"]
                   for t in sk.list_openai_tools()}
        fn = by_name["read_qq_chat"]
        self.assertIn("读取", fn["description"])
        params = fn["parameters"]
        # 只读：不要求发消息正文，也没有外发动作参数
        self.assertEqual(params["required"], ["search_keyword"])
        self.assertNotIn("message", params["properties"])
        self.assertIn("max_lines", params["properties"])
        self.assertIn("verify_ocr", params["properties"])
        # 不给 Python 闭包 verify（tool_call 传不了）
        self.assertNotIn("verify", params["properties"])
        self.assertIs(params["properties"]["verify_ocr"].get("default"), True)

    def test_web_skills_schema_shape(self):
        """browser_search/browser_extract：联网只读技能，schema 齐全且无外发参数"""
        sk = SkillLibrary()
        by_name = {t["function"]["name"]: t["function"]
                   for t in sk.list_openai_tools()}
        fn = by_name["browser_search"]
        self.assertIn("搜索", fn["description"])
        self.assertEqual(fn["parameters"]["required"], ["query"])
        self.assertIn("engine", fn["parameters"]["properties"])
        self.assertEqual(fn["parameters"]["properties"]["engine"]["default"], "bing")

        fn = by_name["browser_extract"]
        self.assertIn("URL", fn["description"])
        self.assertEqual(fn["parameters"]["required"], ["url"])
        self.assertIn("selector", fn["parameters"]["properties"])

    def test_os_service_api_passthrough(self):
        api = OSServiceAPI()
        self.assertEqual(
            api.openai_skill_names(),
            ["app_send_message", "read_qq_chat", "send_email",
             "browser_search", "browser_extract"])
        self.assertTrue(api.is_agent_skill("app_send_message"))
        self.assertTrue(api.is_agent_skill("send_email"))
        self.assertFalse(api.is_agent_skill("system_info"))
        self.assertEqual(len(api.list_skills_openai()), 5)

    def test_agent_toolset_includes_skill(self):
        """模型拿到的是 59 个底层工具 + 5 个白名单技能（总数 64）"""
        with tempfile.TemporaryDirectory(prefix="mini_ats_") as tmp:
            agent = Agent(config=_cfg(tmp))
            registry = agent.api.list_tools_openai()
            merged = registry + agent.api.list_skills_openai()
            names = [t["function"]["name"] for t in merged]
        self.assertEqual(len(registry), 63)                 # registry：61 + browser_refresh/bring_to_front
        self.assertEqual(len(names), 68)
        self.assertIn("app_send_message", names)
        self.assertIn("send_email", names)
        self.assertIn("read_qq_chat", names)
        self.assertIn("browser_search", names)
        self.assertIn("browser_extract", names)

    def test_system_prompt_steers_im_send(self):
        with tempfile.TemporaryDirectory(prefix="mini_ats_") as tmp:
            agent = Agent(config=_cfg(tmp))
            self.assertIn("app_send_message", agent.system_prompt)
            self.assertIn("绝不拆成", agent.system_prompt)


class TestAgentSkillDispatch(unittest.TestCase):
    """_execute_one 对白名单技能走技能分发 + 默认 OCR 门注入 + HIGH 确认门"""

    def _agent(self, tmp, api, confirm):
        return Agent(config=_cfg(tmp, confirm=confirm), api=api)

    def test_skill_goes_run_skill_and_forces_verify_ocr(self):
        with tempfile.TemporaryDirectory(prefix="mini_ats_") as tmp:
            api = _CaptureAPI()
            agent = self._agent(tmp, api, confirm=False)
            r = agent._execute_one(
                "app_send_message",
                {"app_name": "QQ", "search_keyword": "一中兄弟会", "message": "你好"},
            )
        self.assertTrue(r["success"])
        self.assertEqual(api.skill_calls[0][0], "app_send_message")
        # 模型没显式给 → Agent 强制补 verify_ocr=True（发送前 OCR 核对目标会话）
        self.assertIs(api.skill_calls[0][1]["verify_ocr"], True)

    def test_explicit_verify_ocr_false_not_overridden(self):
        with tempfile.TemporaryDirectory(prefix="mini_ats_") as tmp:
            api = _CaptureAPI()
            agent = self._agent(tmp, api, confirm=False)
            agent._execute_one("app_send_message",
                               {"app_name": "QQ", "search_keyword": "群",
                                "message": "hi", "verify_ocr": False})
        self.assertIs(api.skill_calls[0][1]["verify_ocr"], False)

    def test_non_skill_goes_execute_tool(self):
        with tempfile.TemporaryDirectory(prefix="mini_ats_") as tmp:
            api = _CaptureAPI()
            agent = self._agent(tmp, api, confirm=False)
            r = agent._execute_one("list_directory", {"path": "."})
        self.assertTrue(r["success"])
        self.assertEqual(api.tool_calls[0][0], "list_directory")
        self.assertEqual(api.skill_calls, [])

    def test_high_risk_skill_denied(self):
        def deny(_pv):
            raise ConfirmationDenied("app_send_message")

        with tempfile.TemporaryDirectory(prefix="mini_ats_") as tmp:
            api = _CaptureAPI()
            agent = self._agent(tmp, api, confirm=True)
            agent.confirm_handler = deny
            r = agent._execute_one("app_send_message",
                                   {"app_name": "QQ", "search_keyword": "群", "message": "hi"})
        self.assertFalse(r["success"])
        self.assertIn("拒绝", r["error"])
        self.assertEqual(api.skill_calls, [])   # 确认被拒 → 绝不调用技能


class TestOcrDefaultGate(unittest.TestCase):
    """技能内部 verify_ocr 默认 OCR 门：默认项目内视觉桥 / 旧 node 通道 / 缺失明确报错"""

    # 无任何视觉源：主 llm 纯文本(supports_vision=false) 且没配 vision_bridge 段
    NO_VB = {"llm": {"provider": "openai_compatible", "api_key": "k", "supports_vision": False}}
    # 只有独立 vision_bridge 段（纯文本主对话接收者补的第二个视觉 API）
    VB_OK = {"vision_bridge": {"base_url": "https://x/v1", "api_key": "k", "model": "qwen3.5-plus"}}
    # 主 llm 自己有视觉(supports_vision=true)：不配 vision_bridge 也能 OCR
    MAIN_VISION = {"llm": {"provider": "openai_compatible",
                           "api_key": "k", "supports_vision": True}}

    def _app_send(self, skills, extra):
        skills._app = _FakeApp(RECT)
        return skills.call("app_send_message",
                           {"app_name": "QQ", "search_keyword": "一中兄弟会",
                            "message": "你好", **extra})

    def test_verify_ocr_true_without_vision_fails_closed(self):
        """主 llm 纯文本且 vision_bridge 也没配（无任何视觉源、且无外部桥）
        → 明确报错，绝不盲发第一行"""
        with patch.object(skill_mod, "_resolve_vision_js", return_value=None), \
                patch("core.vision_bridge.load_config", return_value=self.NO_VB):
            r = self._app_send(SkillLibrary(), {"verify_ocr": True})
        self.assertFalse(r["success"])
        self.assertIn("视觉桥", r["error"])

    def test_make_ocr_verify_missing_vision_raises(self):
        with patch.object(skill_mod, "_resolve_vision_js", return_value=None), \
                patch("core.vision_bridge.load_config", return_value=self.NO_VB):
            with self.assertRaises(RuntimeError):
                skill_mod.make_ocr_verify("群", RECT, vision_js=None)

    def test_make_ocr_verify_default_project_bridge_matches(self):
        """主 llm 纯文本 → OCR 门走 vision_bridge 段（第二个视觉 API）：
        标题含关键词→True；不含→False（patch，不联网）"""
        with patch.object(skill_mod, "_crop_title_band", return_value="crop.png"), \
                patch("core.vision_bridge.load_config", return_value=self.VB_OK), \
                patch("core.vision_bridge.describe_image",
                      return_value="聊天: 一中兄弟会之大压抑时代") as m:
            verify = skill_mod.make_ocr_verify("一中兄弟会", RECT)   # vision_js=None → 项目内桥
            self.assertTrue(verify("shot.png"))
            m.return_value = "其他会话"
            self.assertFalse(verify("shot.png"))

    def test_make_ocr_verify_uses_main_llm_when_it_has_vision(self):
        """主 llm.supports_vision=true → 不配 vision_bridge 也能构造 OCR 门，
        直接由主模型看图比对（patch describe_image，不联网）"""
        with patch.object(skill_mod, "_crop_title_band", return_value="crop.png"), \
                patch("core.vision_bridge.load_config", return_value=self.MAIN_VISION), \
                patch("core.vision_bridge.describe_image",
                      return_value="聊天: 一中兄弟会之大压抑时代") as m:
            verify = skill_mod.make_ocr_verify("一中兄弟会", RECT)
            self.assertTrue(verify("shot.png"))
            m.return_value = "别的会话"
            self.assertFalse(verify("shot.png"))

    def test_make_ocr_verify_matches_and_not(self):
        """旧通道（显式 node 桥）仍可用：标题含关键词 → True；不含 → False（patch node，不联网）"""
        with tempfile.NamedTemporaryFile(suffix=".js", delete=False) as f:
            js_path = f.name
        try:
            with patch.object(skill_mod, "_crop_title_band", return_value="crop.png"), \
                    patch("subprocess.run") as m:
                m.return_value = types.SimpleNamespace(
                    stdout="聊天: 一中兄弟会之大压抑时代", stderr="")
                verify = skill_mod.make_ocr_verify("一中兄弟会", RECT, vision_js=js_path)
                self.assertTrue(verify("shot.png"))
                m.return_value.stdout = "其他会话"
                self.assertFalse(verify("shot.png"))
        finally:
            os.unlink(js_path)

    def test_make_ocr_verify_rejects_absent_vision_file(self):
        with self.assertRaises(RuntimeError):
            skill_mod.make_ocr_verify("群", RECT, vision_js=r"C:\definitely\no\vision.js")


if __name__ == "__main__":
    unittest.main()
