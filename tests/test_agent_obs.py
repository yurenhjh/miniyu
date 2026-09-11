# -*- coding: utf-8 -*-
"""Observation 生命周期（P1-A 历史图片裁剪 + P1-B 视觉判定）回归测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import Agent


def _make_agent() -> Agent:
    agent = Agent.__new__(Agent)
    agent.obs_max_history_images = 2
    return agent


def _img_msg(note: str) -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": note},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ],
    }


class TestTrimHistoryImages:
    def test_keeps_all_when_at_cap(self):
        a = _make_agent()
        msgs = [_img_msg("s1"), _img_msg("s2")]
        out = a._trim_history_images(msgs)
        assert len([m for m in out if any("image_url" in p for p in m["content"])]) == 2

    def test_drops_oldest_beyond_cap(self):
        a = _make_agent()
        # s1(旧) s2(旧) s3(旧) s4(新) —— 保留最近 2 张（s3、s4），s1/s2 剥图
        msgs = [_img_msg("s1"), _img_msg("s2"), _img_msg("s3"), _img_msg("s4")]
        out = a._trim_history_images(msgs)
        remaining = [i for i, m in enumerate(out)
                     if any("image_url" in p for p in m["content"])]
        assert remaining == [2, 3], remaining
        # 被剥的旧消息仍保留文字说明，且是浅拷贝、不污染原历史
        assert any(p["type"] == "text" for p in out[0]["content"])
        assert not any("image_url" in p for p in out[0]["content"])
        # 原列表未被修改
        assert any("image_url" in p for p in msgs[0]["content"])

    def test_cap_zero_disables_trim(self):
        a = _make_agent()
        a.obs_max_history_images = 0
        msgs = [_img_msg("s1"), _img_msg("s2"), _img_msg("s3")]
        out = a._trim_history_images(msgs)
        assert len(out) == 3
        assert all(any("image_url" in p for p in m["content"]) for m in out)


class TestBrowserActionNeedsVisual:
    def setup_method(self):
        self.a = _make_agent()

    def test_structured_browser_actions_no_screenshot(self):
        for name in ("browser_snapshot", "browser_find", "browser_type",
                     "browser_wait", "browser_read_text"):
            assert self.a._browser_action_needs_visual(name, {"success": True}) is False, name

    def test_launch_navigate_always_visual(self):
        assert self.a._browser_action_needs_visual("browser_launch", {}) is True
        assert self.a._browser_action_needs_visual("browser_navigate", {}) is True

    def test_click_visual_only_on_state_change(self):
        assert self.a._browser_action_needs_visual("browser_click",
                                                   {"page_changed": True}) is True
        assert self.a._browser_action_needs_visual("browser_click",
                                                   {"url_changed": True}) is True
        assert self.a._browser_action_needs_visual("browser_click",
                                                   {"page_changed": False}) is False
        assert self.a._browser_action_needs_visual("browser_click",
                                                   {}) is False

    def test_desktop_global_always_visual(self):
        for name in ("click_at", "send_text", "send_hotkey", "activate_window"):
            assert self.a._browser_action_needs_visual(name, {"success": True}) is True, name