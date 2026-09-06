"""
test_agent_read_web.py
第4组：QQ『先读后回』只读技能 read_qq_chat + 联网技能(browser_search/browser_extract)
        接入 Agent 白名单的接线测试

背景（为什么有这层）：
    用户反馈 miniyu"不会变通"：让它找 QQ 某群、看聊天回一句话，它说 send 技能只能发
    不能读。根因 = 模型能看到的 QQ 函数只有 app_send_message（发），没有"读"；而模型
    的"思考"范围只有上下文里出现的工具。本批改动把"读"单独做成只读技能 read_qq_chat
    （进目标会话→OCR 转写最近聊天），并把已有 browser_search/browser_extract 提升为
    白名单技能，让"先看再回 / 联网查"成为模型天然可走的路径。

本文件不触碰真 QQ / 真浏览器 / 真视觉桥：技能分发用捕获型 fake API，QQ 原子能力用
    fake app 对象，OCR/转写用 patch 拦截，只验证接线与门逻辑。
"""

import unittest
from unittest.mock import patch

from core import skill_library as skill_mod
from core.agent import Agent
from core.os_service_api import OSServiceAPI
from core.safety import ConfirmationDenied
from core.skill_library import SkillLibrary

RECT = {"left": 100, "top": 100, "right": 900, "bottom": 700,
        "width": 800, "height": 600}

WHITELIST = ["app_send_message", "read_qq_chat", "send_email",
             "browser_search", "browser_extract"]


def _cfg(tmp, confirm=True):
    return {
        "llm": {"provider": "deterministic"},
        "agent": {"max_steps": 15, "confirm_high_risk": confirm, "history_window": 20},
        "memory": {"storage_dir": tmp},
    }


class _CaptureAPI(OSServiceAPI):
    """捕获 run_skill / execute_tool 调用，绝不真执行（防止真开浏览器 / 真读 QQ）"""

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
    """模拟 app_controller 的原子能力：记录点击/输入，read_clipboard 只在 Ctrl+C 命中时返回内容。

    设计成让 _probe_text_field 第一次扫描就命中：点击→send_text(marker)→Ctrl+A→清 clip
    →Ctrl+C 把选中文本拷进 clip→read_clipboard 返回 marker。
    """

    def __init__(self, rect=RECT):
        self.rect = rect
        self._field = ""
        self._clip = ""
        self._sel = None
        self.typed = []

    def find_window(self, process=None, title=None):
        return {"hwnd": 101, "title": process or title or "QQ", "pid": 1}

    def activate_window(self, hwnd):
        return True

    def get_window_rect(self, hwnd):
        return self.rect

    def set_clipboard(self, v):
        self._clip = v

    def read_clipboard(self):
        return self._clip

    def click_at(self, x, y):
        pass

    def send_text(self, t):
        self._field = (self._field or "") + t
        self.typed.append(t)

    def send_hotkey(self, keys):
        if "delete" in keys:
            self._field = ""
        elif "ctrl" in keys and "a" in keys:
            self._sel = self._field
        elif "ctrl" in keys and "c" in keys and self._sel is not None:
            self._clip = self._sel
            self._sel = None

    def take_screenshot(self):
        return "shot.png"


class _RowGate:
    """模拟 OCR 核对门：记录试了哪些行；前 fail_first 行不命中（False），之后命中（True）"""

    def __init__(self, fail_first=0):
        self.calls = []
        self.fail_first = fail_first

    def __call__(self, shot):
        self.calls.append(shot)
        return len(self.calls) > self.fail_first


# ============================================================
# read_qq_chat：白名单 / 风险 / 确认门 / verify_ocr 注入
# ============================================================

class TestReadQqChatExposure(unittest.TestCase):
    def test_read_qq_chat_is_whitelisted_and_registered(self):
        sk = SkillLibrary()
        self.assertIn("read_qq_chat", sk.openai_skill_names())
        self.assertTrue(sk.is_agent_skill("read_qq_chat"))
        self.assertIn("read_qq_chat", sk.list_skills())

    def test_read_qq_chat_meta_is_read_only(self):
        """读取聊天是只读动作：READ_ONLY → 无论哪档授权都不进确认门"""
        from core.safety import should_confirm, AUTHZ_BASE, AUTHZ_FULL, HIGH, READ_ONLY
        meta = SkillLibrary().get_skill_meta("read_qq_chat")
        self.assertEqual(meta["risk"], READ_ONLY)
        # 即便 base 档，非 HIGH 也从不需要确认
        self.assertFalse(should_confirm(AUTHZ_BASE, "read_qq_chat", meta["risk"]))
        self.assertFalse(should_confirm(AUTHZ_FULL, "read_qq_chat", HIGH))

    def test_agent_forces_verify_ocr_for_read(self):
        """read_qq_chat 与 app_send_message 一样：模型没给时 Agent 强制补 verify_ocr=True
        （读错会话 = 读到错误信息，同样不对）"""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="mini_rw_") as tmp:
            api = _CaptureAPI()
            agent = Agent(config=_cfg(tmp, confirm=False), api=api)
            r = agent._execute_one(
                "read_qq_chat",
                {"app_name": "QQ", "search_keyword": "一中兄弟会", "max_lines": 10},
            )
        self.assertTrue(r["success"])
        self.assertEqual(api.skill_calls[0][0], "read_qq_chat")
        self.assertIs(api.skill_calls[0][1]["verify_ocr"], True)
        self.assertEqual(api.skill_calls[0][1]["search_keyword"], "一中兄弟会")

    def test_read_qq_chat_read_only_no_confirm_even_deny_handler(self):
        """READ_ONLY → 不触发确认门；即使 confirm_handler 会拒绝，也直接执行"""
        def deny(_pv):
            raise ConfirmationDenied("read_qq_chat")

        import tempfile
        with tempfile.TemporaryDirectory(prefix="mini_rw_") as tmp:
            api = _CaptureAPI()
            agent = Agent(config=_cfg(tmp, confirm=True), api=api)
            agent.confirm_handler = deny
            r = agent._execute_one("read_qq_chat",
                                   {"search_keyword": "群", "verify_ocr": False})
        self.assertTrue(r["success"])
        self.assertEqual(api.skill_calls[0][0], "read_qq_chat")


# ============================================================
# read_qq_chat：功能执行（fake app + patch OCR/转写，不真开 QQ/视觉）
# ============================================================

class TestReadQqChatExecution(unittest.TestCase):
    def _skills_with_fake_app(self):
        sk = SkillLibrary()
        sk._app = _FakeApp(RECT)
        return sk

    def test_read_returns_transcript_and_never_sends(self):
        """进入目标会话 → 转写最近聊天返回；结果里没有 message、也没有任何外发字段"""
        gate = _RowGate()
        transcript = "张三: 今晚几点碰头？\n李四: 8点老地方。"
        sk = self._skills_with_fake_app()
        with patch.object(skill_mod, "make_ocr_verify",
                          side_effect=lambda kw, rect, vision_js=None: gate), \
                patch.object(skill_mod, "transcribe_chat_region",
                             return_value=transcript):
            r = sk.call("read_qq_chat",
                        {"app_name": "QQ", "search_keyword": "一中兄弟会", "max_lines": 10})
        self.assertTrue(r["success"], r.get("error"))
        res = r["result"]
        self.assertEqual(res["transcript"], transcript)
        self.assertEqual(res["conversation"], "一中兄弟会")
        self.assertTrue(res["verify_result"])
        self.assertNotIn("message", res)          # 只读：没有发消息结果
        self.assertEqual(len(gate.calls), 1)      # 第一行就命中 → 只核对一次

    def test_read_retries_rows_until_title_matches(self):
        """前 2 行 OCR 不命中（不是目标群）→ 继续试第 3 行；试完仍不中会报错不读"""
        gate = _RowGate(fail_first=2)
        sk = self._skills_with_fake_app()
        with patch.object(skill_mod, "make_ocr_verify",
                          side_effect=lambda kw, rect, vision_js=None: gate), \
                patch.object(skill_mod, "transcribe_chat_region",
                             return_value="内容"):
            r = sk.call("read_qq_chat", {"search_keyword": "一中兄弟会"})
        self.assertTrue(r["success"], r.get("error"))
        self.assertTrue(r["result"]["verify_result"])
        self.assertEqual(len(gate.calls), 3)      # 试了 3 行

    def test_read_fails_closed_when_no_row_matches(self):
        """目标群不在搜索结果前几行 → 明确报错，绝不去读一个别的会话"""
        gate = _RowGate(fail_first=99)
        sk = self._skills_with_fake_app()
        with patch.object(skill_mod, "make_ocr_verify",
                          side_effect=lambda kw, rect, vision_js=None: gate), \
                patch.object(skill_mod, "transcribe_chat_region",
                             return_value="内容"):
            r = sk.call("read_qq_chat", {"search_keyword": "找不到的群"})
        self.assertFalse(r["success"])
        self.assertIn("未能", r["error"])

    def test_read_without_vision_source_fails_closed(self):
        """主 llm 纯文本 + 无 vision_bridge + 无外部桥 → 明确报"视觉桥"指引，不假装读到"""
        NO_VB = {"llm": {"provider": "openai_compatible",
                         "api_key": "k", "supports_vision": False}}
        sk = self._skills_with_fake_app()
        with patch.object(skill_mod, "_resolve_vision_js", return_value=None), \
                patch("core.vision_bridge.load_config", return_value=NO_VB):
            r = sk.call("read_qq_chat",
                        {"search_keyword": "群", "verify_ocr": True})
        self.assertFalse(r["success"])
        self.assertIn("视觉桥", r["error"])


# ============================================================
# 联网：browser_search / browser_extract 白名单 + 守则 + mock 对等
# ============================================================

class TestWebSkillsExposure(unittest.TestCase):
    def test_web_skills_whitelisted_with_read_meta(self):
        from core.safety import LOW, READ_ONLY
        sk = SkillLibrary()
        for name in ("browser_search", "browser_extract"):
            self.assertTrue(sk.is_agent_skill(name))
        self.assertEqual(sk.get_skill_meta("browser_search")["risk"], LOW)
        self.assertEqual(sk.get_skill_meta("browser_extract")["risk"], READ_ONLY)

    def test_agent_toolset_and_prompt_steer_web(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="mini_rw_") as tmp:
            agent = Agent(config=_cfg(tmp))
            names = [t["function"]["name"] for t in agent._agent_openai_tools()]
            for n in ("browser_search", "browser_extract", "read_qq_chat"):
                self.assertIn(n, names)
            for token in ("read_qq_chat", "browser_search", "browser_extract",
                          "绝不拆成"):
                self.assertIn(token, agent.system_prompt)

    def test_mock_parity(self):
        """mock 版技能库：数量与白名单与正式版一致，read_qq_chat schema 也不含 message"""
        from mock.mock_skills import MockSkillLibrary
        real = SkillLibrary()
        m = MockSkillLibrary()
        self.assertEqual(len(m.list_skills()), len(real.list_skills()))
        self.assertEqual(m.openai_skill_names(), real.openai_skill_names())
        by_name = {t["function"]["name"]: t["function"] for t in m.list_openai_tools()}
        self.assertIn("read_qq_chat", by_name)
        self.assertNotIn("message", by_name["read_qq_chat"]["parameters"]["properties"])
        # mock 也能直接读出"聊天记录"
        rr = m.read_qq_chat(search_keyword="群")
        self.assertIn("transcript", rr)


if __name__ == "__main__":
    unittest.main()
