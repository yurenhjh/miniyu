"""
test_agent_vision.py
第4组：Agent 截图理解（视觉通道） + 过程产物生命周期

覆盖（本文件不真联网、不真截屏，截图用写盘假图替换）：
  1) 坏图修复：_capture_and_add_image 把"文件路径"当 base64 的历史 bug 已修——
     现在读文件→真 base64，作为独立"截图观测"消息加入对话（add_observation_image）；
  2) screen_inspect：Agent 自带视觉能力函数——主模型有视觉返回原图（调用方随后补观测消息），
     无视觉主模型走项目内视觉桥（core.vision_bridge，config.yaml 的 vision_bridge 段）转文字；
     没配好明确报错；
  3) 产物生命周期：截图落当前会话产物目录；用户说"清理截图/清理产物"直接清空；
     /reset 联动清空；本轮产生产物后最终回复附一句"可清理"提醒；
  4) 模型可见函数全集 = 57 底层工具 + app_send_message + screen_inspect；
  5) 统一视觉源 core.vision_bridge：按 llm.supports_vision 自动选源——主对话有视觉
     (true) → 直接用主对话模型看图（不必再配 vision_bridge）；纯文本(false) →
     用 config.yaml 的 vision_bridge 段（独立第二个视觉 API）。两源都没有 /
     请求失败 / 图文件缺失 → VisionBridgeError 明确指引，绝不瞎编。
"""

import base64
import os
import tempfile
import unittest
from unittest.mock import patch

from core import vision_bridge
from core.agent import Agent
from core.conversation import Conversation
from core.llm_client import ChatResponse
from core.os_service_api import OSServiceAPI

PNG_BYTES = b"\x89PNG\r\n\x1a\nFAKE-SCREEN-IMAGE-DATA"


class _VisionBrain:
    """假 LLM：openai_compatible，按参数支持/不支持视觉"""
    provider = "openai_compatible"
    model = "vision-fake"

    def __init__(self, supports_vision=True):
        self.supports_vision = supports_vision


class _ShotAPI(OSServiceAPI):
    """假 API：take_screenshot 在 output 路径写一张假图；其余工具只记录不真执行"""

    def __init__(self, content=PNG_BYTES):
        super().__init__()
        self.content = content
        self.tool_calls = []

    def execute_tool(self, name, params=None):
        params = params or {}
        self.tool_calls.append((name, dict(params)))
        if name == "take_screenshot":
            out = params.get("output") or os.path.join(
                tempfile.gettempdir(), "no_output_screenshot.png")
            with open(out, "wb") as f:
                f.write(self.content)
            return {"success": True, "tool": "take_screenshot", "result": os.path.abspath(out)}
        return {"success": True, "tool": name, "result": {"ok": 1}}


def _cfg(tmp, confirm=False):
    return {
        "llm": {"provider": "deterministic"},
        "agent": {"max_steps": 15, "confirm_high_risk": confirm, "history_window": 20},
        "memory": {"storage_dir": tmp},
    }


class TestConversationObservation(unittest.TestCase):
    """add_observation_image：截图观测是独立消息，不再堆到最早那条 user 消息上"""

    def test_observation_message_shape(self):
        conv = Conversation()
        conv.add_user("帮我操作 QQ")
        b64 = base64.b64encode(PNG_BYTES).decode("ascii")
        conv.add_observation_image("这是当前屏幕截图", b64)

        self.assertEqual(len(conv.messages), 2)
        # 首条 user 消息保持纯文本，不被改写成多模态
        self.assertIsInstance(conv.messages[0]["content"], str)
        obs = conv.messages[1]
        self.assertEqual(obs["role"], "user")
        self.assertEqual(obs["content"][0]["type"], "text")
        self.assertEqual(obs["content"][0]["text"], "这是当前屏幕截图")
        self.assertEqual(obs["content"][1]["type"], "image_url")
        self.assertEqual(
            obs["content"][1]["image_url"]["url"],
            f"data:image/png;base64,{b64}",
        )


class TestCaptureBadImageFix(unittest.TestCase):
    """坏图修复：截图以真实 base64 回传，且文件落当前会话产物目录"""

    def test_capture_reads_real_file_to_base64(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage, \
                tempfile.TemporaryDirectory(prefix="mini_art_") as art:
            os.environ["AGENT_ARTIFACTS_DIR"] = art
            try:
                api = _ShotAPI(PNG_BYTES)
                agent = Agent(config=_cfg(storage), api=api, llm_client=_VisionBrain(True))
                agent.conversation.add_user("打开 QQ 看看")

                path = agent._capture_and_add_image(note="操作后截图")

                self.assertTrue(path and os.path.isfile(path))
                self.assertTrue(path.startswith(os.path.abspath(art)),
                                f"截图应落在产物目录内，实际 {path}")
                # 观测消息里的 data URL 必须是真的图 base64，而不是文件路径字符串
                exp = base64.b64encode(PNG_BYTES).decode("ascii")
                last = agent.conversation.messages[-1]
                self.assertEqual(last["role"], "user")
                self.assertEqual(
                    last["content"][1]["image_url"]["url"],
                    f"data:image/png;base64,{exp}",
                )
                # 产物被登记 → 用于结束提醒
                self.assertEqual(len(agent._turn_artifacts), 1)
            finally:
                os.environ.pop("AGENT_ARTIFACTS_DIR", None)

    def test_cleanup_phrase_and_run_intercept(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage, \
                tempfile.TemporaryDirectory(prefix="mini_art_") as art:
            os.environ["AGENT_ARTIFACTS_DIR"] = art
            try:
                agent = Agent(config=_cfg(storage))  # 离线脑，绝不联网
                d = agent._session_artifact_dir()
                os.makedirs(os.path.join(d, "shots"), exist_ok=True)
                f = os.path.join(d, "shots", "a.png")
                with open(f, "wb") as fh:
                    fh.write(b"x")

                # 无关的话不会被当成清理指令
                self.assertIsNone(agent._try_cleanup_command("整理一下桌面"))
                # 说"清理截图" → 直接清空，不经过 LLM
                msg = agent.run("清理截图")
                self.assertIn("已清理", msg)
                self.assertIn("1 个", msg)
                self.assertFalse(os.path.exists(f))
            finally:
                os.environ.pop("AGENT_ARTIFACTS_DIR", None)

    def test_reset_clears_session_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage, \
                tempfile.TemporaryDirectory(prefix="mini_art_") as art:
            os.environ["AGENT_ARTIFACTS_DIR"] = art
            try:
                agent = Agent(config=_cfg(storage))
                d = agent._session_artifact_dir()
                with open(os.path.join(d, "b.png"), "wb") as fh:
                    fh.write(b"y")
                self.assertTrue(os.path.isdir(d))
                agent.reset()
                self.assertFalse(os.path.isdir(d))   # 重置联动清空产物
                self.assertEqual(len(agent.conversation.messages), 0)
            finally:
                os.environ.pop("AGENT_ARTIFACTS_DIR", None)

    def test_reminder_appended_only_when_artifacts_exist(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage, \
                tempfile.TemporaryDirectory(prefix="mini_art_") as art:
            os.environ["AGENT_ARTIFACTS_DIR"] = art
            try:
                agent = Agent(config=_cfg(storage))
                self.assertEqual(agent._artifact_reminder(), "")
                self.assertEqual(agent._with_reminder("完成"), "完成")

                p = os.path.join(agent._session_artifact_dir(), "shots", "x.png")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(b"z")
                agent._record_artifact(p)
                note = agent._artifact_reminder()
                self.assertIn("清理截图/清理产物", note)
                self.assertTrue(agent._with_reminder("完成").endswith(note))
            finally:
                os.environ.pop("AGENT_ARTIFACTS_DIR", None)


class TestScreenInspect(unittest.TestCase):
    """screen_inspect：Agent 自带"截图理解"能力函数（双通道）"""

    def _agent(self, storage, art, vision):
        os.environ["AGENT_ARTIFACTS_DIR"] = art
        api = _ShotAPI(PNG_BYTES)
        agent = Agent(config=_cfg(storage), api=api, llm_client=_VisionBrain(vision))
        return agent, api

    def test_native_vision_returns_image_lane(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage, \
                tempfile.TemporaryDirectory(prefix="mini_art_") as art:
            os.environ["AGENT_ARTIFACTS_DIR"] = art
            try:
                agent, api = self._agent(storage, art, vision=True)
                agent.conversation.add_user("看看现在界面")
                r = agent._execute_one("screen_inspect", {"question": "当前是什么界面？"})

                self.assertTrue(r["success"])
                self.assertEqual(r["mode"], "image")
                self.assertTrue(os.path.isfile(r["result"]["screenshot_path"]))
                self.assertEqual(api.tool_calls[0][0], "take_screenshot")
                # 原生视觉：_execute 阶段不把图写进 tool 文本；调用方随后补观测消息
                self.assertEqual(len(agent.conversation.messages), 1)
                self.assertIsInstance(agent.conversation.messages[0]["content"], str)
                agent._attach_inspection_observation(r)
                self.assertEqual(len(agent.conversation.messages), 2)
                exp = base64.b64encode(PNG_BYTES).decode("ascii")
                self.assertEqual(
                    agent.conversation.messages[1]["content"][1]["image_url"]["url"],
                    f"data:image/png;base64,{exp}",
                )
            finally:
                os.environ.pop("AGENT_ARTIFACTS_DIR", None)

    def test_text_model_uses_vision_bridge(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage, \
                tempfile.TemporaryDirectory(prefix="mini_art_") as art:
            os.environ["AGENT_ARTIFACTS_DIR"] = art
            try:
                agent, _ = self._agent(storage, art, vision=False)
                agent._run_vision_bridge = lambda _p, _q: "屏幕上是 QQ 会话窗口，标题含 一中兄弟会"
                r = agent._execute_one("screen_inspect", {"question": "现在哪个会话？"})

                self.assertTrue(r["success"])
                self.assertEqual(r["mode"], "text")
                self.assertIn("一中兄弟会", r["result"]["description"])
            finally:
                os.environ.pop("AGENT_ARTIFACTS_DIR", None)

    def test_text_model_without_bridge_fails_explicit(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage, \
                tempfile.TemporaryDirectory(prefix="mini_art_") as art:
            os.environ["AGENT_ARTIFACTS_DIR"] = art
            try:
                agent, _ = self._agent(storage, art, vision=False)
                agent._run_vision_bridge = lambda _p, _q: None
                r = agent._execute_one("screen_inspect", {"question": "看看"})
                self.assertFalse(r["success"])
                self.assertIn("视觉桥", r["error"])
            finally:
                os.environ.pop("AGENT_ARTIFACTS_DIR", None)

    def test_capture_failure_reports_error(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage, \
                tempfile.TemporaryDirectory(prefix="mini_art_") as art:
            os.environ["AGENT_ARTIFACTS_DIR"] = art
            try:
                agent, _ = self._agent(storage, art, vision=True)
                # 截图工具返回 success 但拿不到可读图片文件
                agent.api.execute_tool = lambda _n, _p=None: {
                    "success": True, "tool": "take_screenshot", "result": {"nope": 1}}
                r = agent._execute_one("screen_inspect", {})
                self.assertFalse(r["success"])
                self.assertIn("截屏失败", r["error"])
            finally:
                os.environ.pop("AGENT_ARTIFACTS_DIR", None)


class TestAgentVisionToolset(unittest.TestCase):
    """模型可见函数全集 = 57 工具 + app_send_message + screen_inspect（共 59）"""

    def test_agent_toolset_has_screen_inspect(self):
        with tempfile.TemporaryDirectory(prefix="mini_st_") as storage:
            agent = Agent(config=_cfg(storage))
            tools = agent._agent_openai_tools()
            names = [t["function"]["name"] for t in tools]
            self.assertEqual(len(tools), 59)
            self.assertIn("screen_inspect", names)
            self.assertIn("app_send_message", names)
            # screen_inspect 不是 SkillLibrary 技能（不改变 57/24 计数）
            self.assertFalse(agent.api.is_agent_skill("screen_inspect"))
            self.assertEqual(len(agent.api.list_skills_openai()), 1)


class TestProjectVisionBridge(unittest.TestCase):
    """core.vision_bridge：统一视觉源 —— llm.supports_vision=true→主模型看图（不必配第二个）；
    false→vision_bridge 独立段；两源都没有→明确指引报错"""

    # 场景1：只有独立 vision_bridge（第二个视觉 API），主 llm 缺失/纯文本
    VB_ONLY = {"vision_bridge": {"base_url": "https://dashscope.example/v1",
                                 "api_key": "k", "model": "qwen-vl-max"}}
    # 场景2：主 llm 自己有视觉（supports_vision=true）——不配 vision_bridge 也能看图
    MAIN_VISION = {"llm": {"provider": "openai_compatible",
                           "base_url": "https://llm.example/v1",
                           "api_key": "kllm", "model": "qwen3.5-plus",
                           "supports_vision": True}}
    # 场景3：主 llm 纯文本 + 没配 vision_bridge → 没有任何可用视觉源
    TEXT_NO_BRIDGE = {"llm": {"provider": "openai_compatible",
                              "api_key": "k", "supports_vision": False}}
    BLANK_VB = {"vision_bridge": {"base_url": "", "api_key": "", "model": ""}}

    def test_require_vision_ok_with_vision_bridge_section(self):
        vb = vision_bridge.require_vision(self.VB_ONLY)
        self.assertEqual(vb["model"], "qwen-vl-max")

    def test_require_vision_prefers_vision_llm(self):
        """主 llm.supports_vision=true → 选中的视觉源就是 llm 段本身，不需要第二个 key"""
        sec = vision_bridge.require_vision(self.MAIN_VISION)
        self.assertEqual(sec["model"], "qwen3.5-plus")
        self.assertIs(sec["supports_vision"], True)

    def test_require_vision_raises_when_no_source(self):
        """主 llm 纯文本 且 没配 vision_bridge → 抛指引（提示两种补法）"""
        with self.assertRaises(vision_bridge.VisionBridgeError) as cm:
            vision_bridge.require_vision(self.TEXT_NO_BRIDGE)
        self.assertIn("视觉桥", str(cm.exception))
        self.assertIn("vision_bridge", str(cm.exception))
        self.assertIn("supports_vision", str(cm.exception))

    def test_require_vision_raises_when_fields_blank(self):
        with self.assertRaises(vision_bridge.VisionBridgeError) as cm:
            vision_bridge.require_vision(self.BLANK_VB)
        self.assertIn("vision_bridge", str(cm.exception))
        self.assertIn("api_key", str(cm.exception))

    def test_describe_image_goes_to_vision_bridge_when_main_text_only(self):
        """主 llm 纯文本 → 图发给 vision_bridge 段的独立视觉 API（side_effect 触发真实构造）"""
        holder = {}

        class _FakeVClient:
            def __init__(self, **kw):
                holder["kw"] = kw  # 捕获构造参数：应来自 vision_bridge 段，而非主 llm

            def chat(self, messages=None, tools=None):
                holder["messages"] = messages
                return ChatResponse(text="标题: 一中兄弟会之大压抑时代", raw={"ok": True})

        with patch("core.vision_bridge.OpenAICompatibleClient", side_effect=_FakeVClient):
            text = vision_bridge.describe_image(
                PNG_BYTES, "原样输出这一横条里的标题", config=self.VB_ONLY)
        self.assertIn("一中兄弟会", text)
        self.assertEqual(holder["kw"]["model"], "qwen-vl-max")
        self.assertEqual(holder["kw"]["api_key"], "k")
        self.assertEqual(holder["kw"]["base_url"], "https://dashscope.example/v1")
        content = holder["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "image_url")
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(content[1]["text"], "原样输出这一横条里的标题")

    def test_describe_image_goes_to_main_llm_when_it_has_vision(self):
        """llm.supports_vision=true → 图直接交给主对话(llm)段，不建 vision_bridge 客户端"""
        holder = {}

        class _FakeMClient:
            def __init__(self, **kw):
                holder["kw"] = kw

            def chat(self, messages=None, tools=None):
                holder["messages"] = messages
                return ChatResponse(text="屏幕上是 QQ 会话窗口，标题含 一中兄弟会", raw={"ok": True})

        with patch("core.vision_bridge.OpenAICompatibleClient", side_effect=_FakeMClient):
            text = vision_bridge.describe_image(
                PNG_BYTES, "现在哪个会话？", config=self.MAIN_VISION)
        self.assertIn("QQ", text)
        self.assertEqual(holder["kw"]["model"], "qwen3.5-plus")
        self.assertEqual(holder["kw"]["api_key"], "kllm")
        self.assertEqual(holder["kw"]["base_url"], "https://llm.example/v1")

    def test_describe_image_raises_when_no_vision_source(self):
        with self.assertRaises(vision_bridge.VisionBridgeError):
            vision_bridge.describe_image(PNG_BYTES, "q", config=self.TEXT_NO_BRIDGE)

    def test_describe_image_raises_on_provider_error(self):
        class _ErrClient:
            def chat(self, messages=None, tools=None):
                return ChatResponse(text="❌ API 请求失败（HTTP 401）", raw=None)  # 无 raw=出错路径

        with patch("core.vision_bridge.OpenAICompatibleClient", return_value=_ErrClient()):
            with self.assertRaises(vision_bridge.VisionBridgeError):
                vision_bridge.describe_image(PNG_BYTES, "q", config=self.VB_ONLY)

    def test_describe_image_missing_file_raises(self):
        with self.assertRaises(vision_bridge.VisionBridgeError):
            vision_bridge.describe_image(r"Z:\definitely\no\such.png", "q", config=self.VB_ONLY)


if __name__ == "__main__":
    unittest.main()
