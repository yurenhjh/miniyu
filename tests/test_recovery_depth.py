# -*- coding: utf-8 -*-
"""test_recovery_depth.py — recovery_depth v1（heuristic）聚合器测试

gtp 定义的 6 个边界口径：
  1. failure 后没有新 LLM round        → depth = 0
  2. failure 后 1 个 LLM round         → depth = 1
  3. failure 后多个 LLM rounds         → 正确计数
  4. 两次 failure 的 recovery 区间分段 → 逐失败独立 llm_rounds
  5. failure 后任务立即结束            → depth = 0
  6. 后续 action 成功但无新 LLM round  → depth = 0

recovery_depth_v1 是 heuristic metric（被动推断），不修改任何 runtime；
且与 token 记账（benchmark_valid）相互独立、互不污染。
"""
import json
import os
import tempfile
import unittest

from core.benchmark import aggregate_run_log


def _llm(prompt, completion, cum, step=1):
    return {"kind": "llm", "step": step, "prompt_tokens": prompt,
            "completion_tokens": completion, "total_tokens": prompt + completion,
            "reasoning_tokens": 0, "image_tokens": 0, "image_count": 0,
            "message_count": 10, "char_count": 100, "cum_total_used": cum}


def _act(tool, ok=True, step=1):
    return {"kind": "action", "tool": tool, "ok": ok, "args": "{}",
            "result": "" if ok else "boom", "elapsed_ms": 100, "step": step}


def _log(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _steps(s):
    return [it["llm_rounds"] for it in s["recovery_steps"]]


class TestRecoveryDepth(unittest.TestCase):

    def test_failure_then_no_new_llm_depth0(self):
        # 例1：失败是最后一步，之后无新 LLM → depth=0，且保留一条 cause 记录
        rows = [_llm(100, 10, 110, step=1),
                _act("browser_type", ok=False, step=1)]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["recovery_depth"], 0)
        self.assertEqual(_steps(s), [0])
        self.assertEqual(s["recovery_steps"][0]["cause_tool"], "browser_type")
        self.assertEqual(s["recovery_steps"][0]["cause_step"], 1)

    def test_failure_then_one_llm_round_depth1(self):
        # 例2：失败后恰好 1 个 LLM round → depth=1
        rows = [_llm(100, 10, 110, step=1),
                _act("browser_type", ok=False, step=1),
                _llm(100, 10, 220, step=2),
                _act("browser_find", ok=True, step=2)]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["recovery_depth"], 1)
        self.assertEqual(_steps(s), [1])

    def test_failure_then_multiple_llm_rounds(self):
        # 例3：失败后连续多个 LLM rounds → 正确累加
        rows = [_llm(100, 10, 110, step=1),
                _act("browser_type", ok=False, step=1),
                _llm(100, 10, 220, step=2),   # 重规划 1
                _act("browser_find", ok=True, step=2),
                _llm(100, 10, 330, step=3),   # 重规划 2
                _act("browser_type", ok=True, step=3)]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["recovery_depth"], 2)
        self.assertEqual(_steps(s), [2])

    def test_two_failures_segmented(self):
        # 例4：两次失败分段——每段独立计 llm_rounds，随后的一次失败会截断前一段
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_type", ok=False, step=1),   # 失败 A
            _llm(100, 10, 220, step=2),               # A 的恢复 1
            _act("browser_click", ok=False, step=2),  # 失败 B（截断 A）
            _llm(100, 10, 330, step=3),               # B 的恢复 1
            _act("browser_read_text", ok=True, step=3),
            _llm(100, 10, 440, step=4),               # B 的恢复 2（正常收尾）
        ]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(len(s["recovery_steps"]), 2)
        self.assertEqual(_steps(s), [1, 2])
        self.assertEqual(s["recovery_depth"], 3)
        self.assertEqual(s["recovery_steps"][0]["cause_tool"], "browser_type")
        self.assertEqual(s["recovery_steps"][1]["cause_tool"], "browser_click")

    def test_failure_then_task_ends_immediately(self):
        # 例5：失败后任务立即结束，无任何后续 LLM/action → depth=0
        rows = [_llm(50, 5, 55, step=1),
                _act("browser_click", ok=False, step=1)]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["recovery_depth"], 0)

    def test_failure_then_success_action_but_no_llm(self):
        # 例6：失败后仅一个成功 action 且无新 LLM → LLM 未被强迫重规划 → depth=0
        rows = [_llm(50, 5, 55, step=1),
                _act("browser_type", ok=False, step=1),
                _act("browser_find", ok=True, step=1)]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["recovery_depth"], 0)
        self.assertEqual(_steps(s), [0])

    def test_deduped_llm_not_double_counted(self):
        # 失败后同一 LLM 请求被双写（同 cum/total）→ 只算一次，depth 不虚增
        rows = [_llm(100, 10, 110, step=1),
                _act("browser_type", ok=False, step=1),
                _llm(100, 10, 220, step=2),
                _llm(100, 10, 220, step=3)]  # 双写
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["llm_calls"], 2)
        self.assertEqual(s["recovery_depth"], 1)
        self.assertEqual(_steps(s), [1])

    def test_recovery_does_not_break_token_accounting(self):
        # 关键：recovery_depth 与 benchmark_valid / token 记账相互独立、互不污染。
        rows = [
            _llm(1000, 200, 1200, step=1),
            _act("browser_type", ok=True, step=1),
            _act("browser_click", ok=False, step=2),
            _llm(1500, 100, 2800, step=2),   # cum 全程自洽 → valid
            _act("browser_read_text", ok=True, step=3),
            _llm(500, 50, 3350, step=3),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        # token 记账
        self.assertEqual(s["total_tokens"], 1200 + 1600 + 550)
        self.assertTrue(s["benchmark_valid"])
        # recovery 独立存在：失败后直到结束共 2 个 LLM rounds
        self.assertEqual(s["recovery_depth"], 2)
        self.assertEqual(_steps(s), [2])
        self.assertEqual(s["recovery_steps"][0]["cause_tool"], "browser_click")
        # 结构化证据路径不受 recovery 影响（末 action 为整页 read → whole_page）
        self.assertEqual(s["verification_source"], "whole_page_read")

    def test_empty_run_recovery_zero(self):
        p = _log([])
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["recovery_depth"], 0)
        self.assertEqual(s["recovery_steps"], [])


if __name__ == "__main__":
    unittest.main()