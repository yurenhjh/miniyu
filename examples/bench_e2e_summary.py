# -*- coding: utf-8 -*-
"""bench_e2e_summary.py — E2E Benchmark 汇总入口（instrumentation-only）

用法：
  python examples/bench_e2e_summary.py <run_log.jsonl> [--task-success] [--out out.json]
  python examples/bench_e2e_summary.py --latest [root] [--task-success]

不修改任何运行路径：只读 run_log，产出 Benchmark Summary。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.benchmark import aggregate_run_log, find_latest_run_log, format_summary, write_summary_json


def _default_artifacts_dir():
    for p in ("conversations", str(Path(__file__).resolve().parent.parent / "conversations")):
        if os.path.isdir(p):
            return p
    return str(Path(__file__).resolve().parent.parent)


def main():
    ap = argparse.ArgumentParser(description="E2E Benchmark 汇总（只读 run_log）")
    ap.add_argument("path", nargs="?", help="run_log_*.jsonl 路径；配合 --latest 可省略")
    ap.add_argument("--latest", action="store_true", help="自动选取最新一份 run_log")
    ap.add_argument("--root", default=None, help="--latest 的搜索根目录（默认 conversations/）")
    ap.add_argument("--task-success", action="store_true", help="标记该任务目标达成（task_success=True）")
    ap.add_argument("--out", default=None, help="输出 summary JSON 的路径（默认写到 run_log 同目录）")
    args = ap.parse_args()

    path = args.path
    if args.latest or not path:
        root = args.root or _default_artifacts_dir()
        path = find_latest_run_log(root)
        if not path:
            print("未找到 run_log_*.jsonl（root=%s）" % root)
            return 1
        print("[latest] %s" % path)
    else:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            print("文件不存在：", path)
            return 1

    task_success = True if args.task_success else None
    summary = aggregate_run_log(path, task_success=task_success)
    print(format_summary(summary))

    out = args.out
    if not out:
        out = os.path.splitext(path)[0] + ".bench.json"
    write_summary_json(out, summary)
    print("\n[summary] %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())