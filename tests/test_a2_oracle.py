# -*- coding: utf-8 -*-
"""A2 benchmark 判定 oracle 的单元测试（仅测判定层，不启动浏览器/Agent）。

验证 run_a2._oracle_hit/_normalize_answer:
  - 纯文本正确输出 → 命中
  - Markdown 粗体/斜体/下划线强调 → 与纯文本等价（不误杀）
  - “北京北京/天气未知”“只说晴”“空/None” → 不误判为成功
禁止宽松规则（只找“晴”）是设计约束的一部分。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "examples" / "browser_benchmark"))
import run_a2  # noqa: E402


def test_plain_text_hit():
    a = "查询完成：城市：北京 天气：晴 温度：26℃"
    assert run_a2._oracle_hit(a) is True


def test_markdown_bold_equivalent():
    a = "**城市**：北京\n**天气**：晴\n**温度**：26℃"
    assert run_a2._normalize_answer(a) == "城市：北京 天气：晴 温度：26℃"
    assert run_a2._oracle_hit(a) is True


def test_markdown_italic_and_underline_equivalent():
    a = "*城市*：北京 __天气__：晴 _温度_：26℃"
    assert run_a2._oracle_hit(a) is True


def test_double_bold_and_bold_italic_not_break():
    a = "***城市***：北京\n***天气***：晴\n***温度***：26℃"
    assert run_a2._oracle_hit(a) is True


def test_reject_polluted_input():
    # 输入污染“北京北京”→ 天气未知 —— 不得误判成功
    a = "城市：北京北京 天气：未知 温度：—"
    assert run_a2._oracle_hit(a) is False


def test_reject_single_word_only():
    # 只含“晴”→ 三要素未全命中 → 不得判成功
    a = "晴"
    assert run_a2._oracle_hit(a) is False


def test_reject_missing_one_element():
    a = "城市：北京 天气：晴"   # 缺温度
    assert run_a2._oracle_hit(a) is False


def test_normalize_handles_none_and_empty():
    assert run_a2._normalize_answer(None) == ""
    assert run_a2._normalize_answer("") == ""
    assert run_a2._oracle_hit(None) is False
    assert run_a2._oracle_hit("") is False