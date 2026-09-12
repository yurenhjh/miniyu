# -*- coding: utf-8 -*-
"""test_benchmark.py — E2E Benchmark 聚合器测试（instrumentation-only 口径）

覆盖：token 一致性校验、LLM 双写去重、工具计数(成功/失败)、wait 状态、
read 定向/整页与 verification_source、task_success/verified_success 区分。
"""
import json
import os
import tempfile
import unittest

from core.benchmark import aggregate_run_log, find_latest_run_log


def _llm(prompt, completion, cum, reason=0, image=0, img_count=0, step=1):
    return {"kind": "llm", "step": step, "prompt_tokens": prompt,
            "completion_tokens": completion, "total_tokens": prompt + completion,
            "reasoning_tokens": reason, "image_tokens": image, "image_count": img_count,
            "message_count": 10, "char_count": 100, "cum_total_used": cum}


def _act(tool, ok=True, args=None, result="", elapsed=1.0, step=1):
    return {"kind": "action", "tool": tool, "ok": ok, "args": json.dumps(args or {}),
            "result": result, "elapsed_ms": elapsed * 1000, "step": step}


def _run_start(initial_cum):
    return {"kind": "run_start", "ts": "0", "initial_cum_total_used": initial_cum}


def _log(rows):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


class TestBenchmark(unittest.TestCase):

    def test_clean_structured_read_run(self):
        rows = [
            _llm(1000, 200, 1200, reason=50, image=30, img_count=1, step=1),
            _act("browser_launch", True, {}, step=1),
            _act("browser_navigate", True, {"url": "x"}, step=1),
            _act("browser_find", True, {"role": "textbox"}, step=1),
            _act("browser_type", True, {"target": "e1", "text": "你好", "press_enter": True}, step=2),
            _act("browser_wait_for_change", True, {"timeout": 60}, result="COMPLETED", step=3),
            _act("browser_read_text", True, {"handle": "e5"}, step=4),
            _llm(1500, 100, 2800, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertTrue(s["benchmark_valid"])
        self.assertEqual(s["llm_calls"], 2)
        self.assertEqual(s["total_tokens"], 1200 + 1600)
        self.assertEqual(s["find"], 1)
        self.assertEqual(s["type"], 1)
        self.assertEqual(s["send"], 1)
        self.assertEqual(s["wait"], 1)
        self.assertEqual(s["wait_completed"], 1)
        self.assertEqual(s["read"], 1)
        self.assertEqual(s["read_targeted"], 1)
        self.assertEqual(s["read_whole_page"], 0)
        self.assertEqual(s["verification_source"], "structured_read")
        self.assertTrue(s["task_success"])
        self.assertTrue(s["verified_success"])

    def test_doublerecord_dedup_reconciles(self):
        # 同一请求双写（同 cum/total）：应去重，llm_calls 不增、sum==cum 校验通过
        rows = [
            _llm(1000, 200, 1200, step=1),
            _llm(1000, 200, 1200, step=2),  # 双写，step 不同也不该重复计
            _llm(500, 50, 1750, step=3),
        ]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["llm_calls"], 2)
        self.assertEqual(s["total_tokens"], 1200 + 550)
        self.assertEqual(s["final_cum_total_used"], 1750)
        self.assertTrue(s["benchmark_valid"])

    def test_discrepancy_marks_invalid(self):
        # 缺口累积：sum(per-call) != cum → benchmark_valid=False
        rows = [_llm(1000, 200, 2000, step=1), _llm(500, 50, 2050, step=2)]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertFalse(s["benchmark_valid"])
        self.assertNotEqual(s["total_tokens"], s["final_cum_total_used"])

    def test_initial_cum_reconciles_offset_counter(self):
        # P2-6 记账：跨运行继承的计数器偏移（initial=10445）。run_total = final - initial
        # 恰好等于 sum(per-call)，即使 sum(per-call) != raw final_cum 也应 valid。
        rows = [
            _run_start(10445),
            _llm(16200, 121, 26766, step=1),      # total 16321, cum=10445+16321
            _llm(16200, 47, 43013, step=2),       # total 16247, cum=26766+16247
            _llm(16200, 59, 59272, step=3),       # total 16259, cum=43013+16259
        ]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["initial_cum_total_used"], 10445)
        self.assertEqual(s["run_total_tokens"], 59272 - 10445)
        self.assertEqual(s["total_tokens"], 16321 + 16247 + 16259)
        self.assertEqual(s["run_total_tokens"], s["total_tokens"])
        self.assertTrue(s["benchmark_valid"])
        # 关键：sum(per-call) 并不等于 raw final_cum，但用 run_total 校验后是合法的
        self.assertNotEqual(s["total_tokens"], s["final_cum_total_used"])

    def test_initial_cum_missing_call_still_invalid(self):
        # P2-6 记账：即使有 initial 基线，若 run 内确有漏记调用（cum 跳变 > 记录之和）
        # 仍必须 invalid —— 不能用 initial 掩盖真实缺口。
        rows = [
            _run_start(0),
            _llm(1000, 200, 1200, step=1),
            _llm(500, 50, 2500, step=2),          # 缺口 1250：1200→2500 跳了 1300，但记录仅 550
        ]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["run_total_tokens"], 2500)
        self.assertEqual(s["total_tokens"], 1200 + 550)
        self.assertFalse(s["benchmark_valid"])

    def test_run_start_absent_legacy(self):
        # 无 run_start 头行的旧日志：initial 视为 0，退化为 sum(per-call)==final_cum 旧口径。
        rows = [_llm(1000, 200, 1200, step=1), _llm(500, 50, 1750, step=2)]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["initial_cum_total_used"], 0)
        self.assertEqual(s["run_total_tokens"], 1750)
        self.assertTrue(s["benchmark_valid"])

    def test_whole_page_read_not_verified(self):
        rows = [
            _llm(1000, 100, 1100, step=1),
            _act("browser_read_text", True, {}, step=1),  # 无目标 → 整页读
            _act("browser_read_text", True, {"handle": "e9"}, step=2),
            _llm(500, 50, 1650, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["read_targeted"], 1)
        self.assertEqual(s["read_whole_page"], 1)
        self.assertEqual(s["verification_source"], "structured_read")
        self.assertTrue(s["verified_success"])

    def test_whole_page_only_source(self):
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_read_text", True, {}, step=1),
            _llm(100, 10, 220, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["verification_source"], "whole_page_read")
        self.assertFalse(s["verified_success"])

    def test_vision_source_when_no_read(self):
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_inspect", True, {"output": "x.png"}, step=1),
            _act("browser_snapshot", True, {}, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["verification_source"], "vision")
        self.assertFalse(s["verified_success"])

    def test_task_fail_blocks_verified(self):
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_read_text", True, {"handle": "e1"}, step=1),
            _llm(100, 10, 220, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=False)
        os.unlink(p)
        self.assertEqual(s["verification_source"], "structured_read")
        self.assertFalse(s["verified_success"])

    def test_read_latest_reply_is_structured_read(self):
        """P2-6：browser_read_latest_reply 成功 → 判定 structured_read，verified_success=True。
        （这是第二轮 E2E 的真实读法：用新工具而非整页 browser_read_text。）"""
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_wait_for_change", True, {}, result="COMPLETED", step=1),
            _act("browser_read_latest_reply", True, {}, result='{"success":true,"text":"你好","source":"structured_read"}', step=2),
            _llm(100, 10, 220, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["read_latest_reply"], 1)
        self.assertEqual(s["read_ok_latest_reply"], 1)
        self.assertEqual(s["verification_source"], "structured_read")
        self.assertTrue(s["verified_success"])
        self.assertEqual(s["wait_completed"], 1)

    def test_wait_delta_is_structured_read(self):
        """P2-6 C：browser_wait_for_change 返回结构化 message_delta → 也该 verified_success=true，
        path=wait_delta。它与 read_latest_reply 同一引擎，不应判成 model_inference 误报。"""
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_wait_for_change", True, {},
                 result='{"success":true,"state":"COMPLETED","delta_detected":true,'
                        '"message_delta":"你好呀，有什么想聊的吗？"}', step=1),
            _llm(100, 10, 220, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["read_ok_wait_delta"], 1)
        self.assertEqual(s["verification_source"], "structured_read")
        self.assertEqual(s["verification_path"], "wait_delta")
        self.assertTrue(s["verified_success"])

    def test_wait_evidence_self_declared_without_delta_text(self):
        """P2-6 C：wait 以 evidence.source=semantic_delta 自声明（无 message_delta）也应识别。"""
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_wait_for_change", True, {},
                 result='{"success":true,"evidence":{"source":"semantic_delta","verified":true}}', step=1),
            _llm(100, 10, 220, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["verification_source"], "structured_read")
        self.assertEqual(s["verification_path"], "wait_delta")
        self.assertTrue(s["verified_success"])

    def test_wait_no_delta_no_read_not_verified(self):
        """P2-6 C：wait 完成但无结构化 delta、也无读取 → 退化为 model_inference（不判 verified）。"""
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_wait_for_change", True, {}, result="COMPLETED", step=1),
            _llm(100, 10, 220, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["read_ok_wait_delta"], 0)
        self.assertEqual(s["verification_source"], "model_inference")
        self.assertEqual(s["verification_path"], "model_inference")
        self.assertFalse(s["verified_success"])

    def test_wait_delta_then_latest_reply_prefers_latest_reply(self):
        """P2-6 C：同时有 wait_delta 与显式 read_latest_reply → 显式读取为更权威证据。"""
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_wait_for_change", True, {},
                 result='{"success":true,"delta_detected":true,"message_delta":"你好"}', step=1),
            _act("browser_read_latest_reply", True, {},
                 result='{"success":true,"text":"你好","source":"structured_read"}', step=2),
            _llm(100, 10, 230, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["verification_path"], "latest_reply")
        self.assertTrue(s["verified_success"])

    def test_wait_delta_beats_whole_page_read(self):
        """P2-6 C：wait_delta（结构化）优先于整页读取（weak）。"""
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_wait_for_change", True, {},
                 result='{"success":true,"delta_detected":true,"message_delta":"你好"}', step=1),
            _act("browser_read_text", True, {}, result="整页文本", step=2),
            _llm(100, 10, 230, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p, task_success=True)
        os.unlink(p)
        self.assertEqual(s["verification_source"], "structured_read")
        self.assertEqual(s["verification_path"], "wait_delta")
        self.assertTrue(s["verified_success"])

    def test_empty_log(self):
        p = _log([])
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["llm_calls"], 0)
        self.assertEqual(s["total_tokens"], 0)
        self.assertIsNone(s["task_success"])
        self.assertFalse(s["verified_success"])
        self.assertEqual(s["verification_source"], "unknown")

    def test_action_failure_counted_as_recovery_proxy(self):
        rows = [
            _llm(100, 10, 110, step=1),
            _act("browser_type", False, {"target": "e1", "text": "你好"}, step=1),
            _act("browser_find", True, {"role": "textbox"}, step=1),
            _llm(100, 10, 220, step=2),
        ]
        p = _log(rows)
        s = aggregate_run_log(p)
        os.unlink(p)
        self.assertEqual(s["action_failures"], 1)
        self.assertEqual(s["llm_recovery_proxy"], 1)

    def test_find_latest(self):
        root = tempfile.mkdtemp()
        sub = os.path.join(root, "conversations")
        os.makedirs(sub, exist_ok=True)
        for t in ("100", "200"):
            with open(os.path.join(sub, "run_log_%s.jsonl" % t), "w") as f:
                f.write("\n")
        self.assertTrue(find_latest_run_log(root).endswith("run_log_200.jsonl"))


if __name__ == "__main__":
    unittest.main()