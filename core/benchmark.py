# -*- coding: utf-8 -*-
"""
benchmark.py — P2-5 之后的 Browser Agent E2E Benchmark 聚合器（instrumentation-only）

只读 run_log_*.jsonl（kind in {action, llm}），统计并汇总一次完整任务的观测口径。
**不改变运行路径**：不改 System Prompt / browser controller 行为 / 任何工具 / wait /
semantic delta / SoM / recovery 策略。仅做统计、日志、一致性校验与 Summary 输出。

行来源（core/agent.py）：
  kind='llm'    —— 每次 LLM 请求：prompt/completion/total/reasoning/image/cum_total_used
  kind='action' —— 每次工具/技能调用：tool / args(截断) / ok / elapsed_ms
  kind='benchmark' —— run 结束的一致性校验行（sum(per-call)==cum 是否一致）

类别口径（与 gtp 评审 §二 对齐，均来自 action 行的 tool 名 + args 判定）：
  find=browser_find              type=browser_type
  send=browser_type(press_enter) click=浏览器点击(非坐标)
  wait=browser_wait/_for_change  read=browser_read_text
  inspect=browser_inspect(SoM)   som/screenshot=browser_snapshot
  selector_fallback=click/type/find 带 selector
  coordinate_click=click_at/mouse_click / click/type 带 x,y
  llm_recovery —— 无专用标记：以「失败 action 数」作为代理，explicit 归类为 action_failures。
"""
import json
import os
import re


# --- 工具类别判定 ---
_WAIT_TOOLS = {"browser_wait", "browser_wait_for_change"}
_READ_TOOLS = {"browser_read_text"}
# P2-6：结构化读取最新回复的工具 —— 成功即证明 structured_read
_STRUCTURED_READ_TOOLS = {"browser_read_latest_reply"}
_CLICK_TOOLS = {"browser_click", "mouse_click"}
_COORD_TOOLS = {"click_at", "mouse_click"}
_INSPECT_TOOLS = {"browser_inspect"}
_SNAPSHOT_TOOLS = {"browser_snapshot"}
_FIND_TOOLS = {"browser_find"}
_TYPE_TOOLS = {"browser_type"}
_LLM_REASON_TOOL_UNSET = object()


def _get(row, key, default=None):
    try:
        v = row.get(key) if isinstance(row, dict) else None
        return default if v is None else v
    except Exception:
        return default


def _load_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    return rows


def _llm_fp(row):
    # 同一 LLM 请求的累计用量(cum) 唯一；用 (cum,total,prompt,completion) 识别同一请求的双写快照。
    # 不含 step：旧日志里同一请求双写时 step 也可能不同，含 step 会导致漏去重。
    return (int(_get(row, "cum_total_used", 0) or 0), int(_get(row, "total_tokens", 0) or 0),
            int(_get(row, "prompt_tokens", 0) or 0), int(_get(row, "completion_tokens", 0) or 0))


def _dedup_llm(llm_rows):
    """丢弃同一请求的双写 llm 行（如流式 finally 与内联快照），保证 token 用量
    不对同一请求重复计。仅在 total>0 时收拢，避免把 0 消耗的边界行误并。"""
    out = []
    last = None
    for r in llm_rows:
        fp = _llm_fp(r)
        if fp == last and int(_get(r, "total_tokens", 0) or 0) > 0:
            continue
        last = fp
        out.append(r)
    return out


def _parse_args(args):
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    try:
        return json.loads(str(args))
    except Exception:
        return {}


def _has_any(a, keys):
    k = {str(x).lower() for x in a.keys()}
    for want in keys:
        if any(want in x for x in k):
            return True
    return False


def _read_is_targeted(a):
    """browser_read_text 是否带定向目标（handle/ref/target/selector/element 任一本为真）。"""
    if not a:
        return False
    for key in ("handle", "ref", "target", "selector", "element", "query"):
        v = a.get(key)
        if v not in (None, "", "", [], {}):
            return True
    return False


def _wait_state(result_s, ok):
    if ok:
        return "completed"
    s = str(result_s or "")
    low = s.lower()
    if "anchor_lost" in low:
        return "anchor_lost"
    if "loading_stuck" in low:
        return "loading_stuck"
    if "timeout" in low or "no_change" in low:
        return "timeout"
    return "failed"


def _wait_delta_evidence(result_s, ok):
    """browser_wait_for_change 是否返回了可信的『结构化 assistant delta』。

    该 delta 与 browser_read_latest_reply 出自同一引擎（_ensure_anchor →
    semantic_blocks → control/chip 过滤 → dedup），因此它本身就是结构化证据，
    可支撑 verified_success，而无须再要求 Agent 显式重复读取。
    判定不靠工具名猜测：需 ok 且 delta_detected 为真，且 message_delta（或
    工具自声明的 evidence.source == semantic_delta）非空。
    """
    if not ok:
        return False
    # 注意：action.result 可能在工具层被截断为不完整 JSON（长 message_blocks 数组被
    # 切断），不能依赖整体 json.loads。用头部标记做稳健识别。
    if isinstance(result_s, dict):
        ev = result_s.get("evidence")
        if isinstance(ev, dict) and ev.get("source") == "semantic_delta":
            return True
        if result_s.get("delta_detected") and result_s.get("success"):
            text = result_s.get("message_delta") or result_s.get("delta") or ""
            return isinstance(text, str) and bool(text.strip())
        return False
    s = str(result_s or "")
    if not s or not s.strip():
        return False
    if '"evidence"' in s and '"source"' in s and '"semantic_delta"' in s:
        return True
    if re.search(r'"success"\s*:\s*true', s) and re.search(r'"delta_detected"\s*:\s*true', s):
        m = re.search(r'"message_delta"\s*:\s*"([^"]*)"', s)
        if m and m.group(1).strip():
            return True
    return False


def aggregate_run_log(path, task_success=None):
    """聚合一条 run_log，返回结构化 Benchmark 统计（dict）。

    task_success: 由操作者/运行判定该任务是否达成目标（真值），None=未知。
    verified_success = task_success and (verification_source == 'structured_read')。
    """
    rows = _load_rows(path)
    llm_all = [r for r in rows if r.get("kind") == "llm"]
    llm = _dedup_llm(llm_all)
    acts = [r for r in rows if r.get("kind") == "action"]
    bench_rows = [r for r in rows if r.get("kind") == "benchmark"]

    # ---- Token ----
    token = {
        "llm_calls": len(llm),
        "llm_calls_raw": len(llm_all),
        "raw_dup_extra": len(llm_all) - len(llm),
        "prompt_tokens": sum(int(_get(r, "prompt_tokens", 0) or 0) for r in llm),
        "completion_tokens": sum(int(_get(r, "completion_tokens", 0) or 0) for r in llm),
        "reasoning_tokens": sum(int(_get(r, "reasoning_tokens", 0) or 0) for r in llm),
        "image_tokens": sum(int(_get(r, "image_tokens", 0) or 0) for r in llm),
        "total_tokens": sum(int(_get(r, "total_tokens", 0) or 0) for r in llm),
        "max_prompt_tokens": max([int(_get(r, "prompt_tokens", 0) or 0) for r in llm] or [0]),
        "max_image_count": max([int(_get(r, "image_count", 0) or 0) for r in llm] or [0]),
    }
    final_cum = int(_get(llm[-1], "cum_total_used", 0) or 0) if llm else 0
    token["final_cum_total_used"] = final_cum

    # P2-6 记账：run 初始累计计数器基线（run_start 头行）。用于消除跨运行继承的
    # cum 偏移：run_total = final_cum - initial_cum 才是本轮真实消耗。
    initial_cum = 0
    for r in rows:
        if r.get("kind") == "run_start":
            try:
                initial_cum = max(initial_cum, int(_get(r, "initial_cum_total_used", 0) or 0))
            except Exception:
                pass
    token["initial_cum_total_used"] = initial_cum
    token["run_total_tokens"] = final_cum - initial_cum

    # ---- Browser actions（按工具名，保留原始名，成功/失败/耗时）----
    tools = {}
    selector_fallback = 0
    coordinate_click = 0
    read_targeted = 0
    read_whole_page = 0
    read_structured_latest = 0
    read_ok = {"targeted": 0, "whole_page": 0, "structured_latest": 0}
    wait_delta_ok = 0
    wait = {"wait_calls": 0, "completed": 0, "timeout": 0, "anchor_lost": 0,
            "loading_stuck": 0, "failed": 0, "elapsed_ms": 0}
    action_failures = 0

    for a in acts:
        name = str(a.get("tool") or "")
        ok = bool(a.get("ok"))
        args = _parse_args(a.get("args"))
        elapsed = float(a.get("elapsed_ms") or 0)
        t = tools.setdefault(name, {"calls": 0, "success": 0, "failure": 0, "elapsed_ms": 0})
        t["calls"] += 1
        t["elapsed_ms"] += elapsed
        if ok:
            t["success"] += 1
        else:
            t["failure"] += 1
            action_failures += 1

        if name in _WAIT_TOOLS:
            wait["wait_calls"] += 1
            wait["elapsed_ms"] += elapsed
            st = _wait_state(a.get("result"), ok)
            if st in ("completed",):
                wait["completed"] += 1
            elif st == "timeout":
                wait["timeout"] += 1
            elif st == "anchor_lost":
                wait["anchor_lost"] += 1
            elif st == "loading_stuck":
                wait["loading_stuck"] += 1
            else:
                wait["failed"] += 1
            if name == "browser_wait_for_change" and _wait_delta_evidence(a.get("result"), ok):
                wait_delta_ok += 1

        is_click = name in _CLICK_TOOLS
        if name in _COORD_TOOLS or (is_click and (_has_any(args, ("x",)) and _has_any(args, ("y",)))):
            coordinate_click += 1
        if name in (_TYPE_TOOLS | _CLICK_TOOLS | _FIND_TOOLS) and _has_any(args, ("selector",)):
            selector_fallback += 1

        if name in _READ_TOOLS:
            if _read_is_targeted(args):
                read_targeted += 1
                if ok:
                    read_ok["targeted"] += 1
            else:
                read_whole_page += 1
                if ok:
                    read_ok["whole_page"] += 1
        elif name in _STRUCTURED_READ_TOOLS:
            # P2-6：browser_read_latest_reply 本身就是结构化读取，成功即 structured_read
            read_structured_latest += 1
            if ok:
                read_ok["structured_latest"] += 1

    # ---- Verification source & path（P2-6：结构化证据最高优先，且区分途径） ----
    # verified_success 取决于『最终答案是否有可靠的工具结构化证据支撑』，
    # 而非『是否调用了某个特定工具』。tool identity ≠ evidence quality。
    verification_path = "unknown"
    if read_ok["structured_latest"] > 0:
        # 显式调用 browser_read_latest_reply 的结构化读取
        verification_source = "structured_read"
        verification_path = "latest_reply"
    elif wait_delta_ok > 0:
        # browser_wait_for_change 返回的语义 delta（与 read_latest_reply 同引擎）
        verification_source = "structured_read"
        verification_path = "wait_delta"
    elif read_ok["targeted"] > 0:
        verification_source = "structured_read"
        verification_path = "read_targeted"
    elif read_ok["whole_page"] > 0:
        verification_source = "whole_page_read"
        verification_path = "whole_page"
    elif tools.get("browser_inspect", {}).get("success", 0) > 0 or tools.get("browser_snapshot", {}).get("success", 0) > 0:
        verification_source = "vision"
        verification_path = "vision"
    elif token["llm_calls"] > 0:
        verification_source = "model_inference"
        verification_path = "model_inference"
    else:
        verification_source = "unknown"

    # ---- 一致性（以去重后的 per-call sum 对比本轮 run_total） ----
    env_report = None
    for b in bench_rows:
        if "valid" in b:
            env_report = b
    benchmark_valid = None
    note = ""
    if env_report is not None:
        # 优先采信 run 内 _finalize_run_consistency 的权威判定（含 initial_cum）。
        benchmark_valid = bool(env_report["valid"])
        note = str(env_report.get("note") or "")
    elif llm:
        # 旧日志无 run_log 内嵌判定：用本地初始基线（run_start 头行，缺失视为 0，即旧口径）
        per_call_sum = token["total_tokens"]
        run_total = token["run_total_tokens"]
        benchmark_valid = (per_call_sum == run_total)
        if not benchmark_valid:
            note = "INVALID: sum(per-call total_tokens) != (末次 cum - run 初始 cum)，见 run_start / 值"

    # ---- 结果 ----
    verified_success = (task_success is True) and (verification_source == "structured_read")
    summary = {
        "benchmark_valid": benchmark_valid,
        "task_success": task_success,
        "verified_success": verified_success,
        "verification_source": verification_source,
        "verification_path": verification_path,
        "llm_calls": token["llm_calls"],
        "total_tokens": token["total_tokens"],
        "prompt_tokens": token["prompt_tokens"],
        "completion_tokens": token["completion_tokens"],
        "reasoning_tokens": token["reasoning_tokens"],
        "image_tokens": token["image_tokens"],
        "max_prompt_tokens": token["max_prompt_tokens"],
        "max_image_count": token["max_image_count"],
        "initial_cum_total_used": token["initial_cum_total_used"],
        "run_total_tokens": token["run_total_tokens"],
        "final_cum_total_used": final_cum,
        "raw_dup_extra": token["raw_dup_extra"],
        "consistency_note": note,
        "tools": tools,
        "find": tools.get("browser_find", {}).get("calls", 0),
        "type": tools.get("browser_type", {}).get("calls", 0),
        "send": tools.get("browser_type", {}).get("calls", 0),  # type 常带 press_enter 完成发送
        "click": sum(v["calls"] for k, v in tools.items() if k in _CLICK_TOOLS),
        "wait": wait["wait_calls"],
        "read": read_targeted + read_whole_page + read_structured_latest,
        "read_latest_reply": read_structured_latest,
        "read_ok_wait_delta": wait_delta_ok,
        "inspect": tools.get("browser_inspect", {}).get("calls", 0),
        "som_screenshot": tools.get("browser_snapshot", {}).get("calls", 0),
        "selector_fallback": selector_fallback,
        "coordinate_click": coordinate_click,
        "action_failures": action_failures,
        "llm_recovery_proxy": action_failures,
        "wait_completed": wait["completed"],
        "wait_timeout": wait["timeout"],
        "wait_anchor_lost": wait["anchor_lost"],
        "wait_loading_stuck": wait["loading_stuck"],
        "wait_elapsed_ms": round(wait["elapsed_ms"], 1),
        "read_targeted": read_targeted,
        "read_whole_page": read_whole_page,
        "read_ok_targeted": read_ok["targeted"],
        "read_ok_whole_page": read_ok["whole_page"],
        "read_ok_latest_reply": read_ok["structured_latest"],
    }
    return summary


def format_summary(s):
    lines = []
    lines.append("==== Benchmark Summary ====")
    flags = "{benchmark_valid} ts={task_success} vs={verified_success} src={verification_source} path={verification_path}".format(**s)
    lines.append(flags)
    if s.get("consistency_note"):
        lines.append("  note: " + s["consistency_note"])
    lines.append("-- tokens --")
    for k in ("llm_calls", "total_tokens", "prompt_tokens", "completion_tokens",
              "reasoning_tokens", "image_tokens", "max_prompt_tokens", "max_image_count",
              "initial_cum_total_used", "run_total_tokens", "final_cum_total_used"):
        lines.append("  %-22s %s" % (k, s.get(k)))
    lines.append("-- browser actions --")
    for k in ("find", "type", "send", "click", "wait", "read", "inspect",
              "som_screenshot", "selector_fallback", "coordinate_click",
              "action_failures", "llm_recovery_proxy"):
        lines.append("  %-22s %s" % (k, s.get(k)))
    lines.append("-- wait --")
    for k in ("wait_completed", "wait_timeout", "wait_anchor_lost", "wait_loading_stuck", "wait_elapsed_ms"):
        lines.append("  %-22s %s" % (k, s.get(k)))
    lines.append("-- read / verification --")
    for k in ("read_targeted", "read_whole_page", "read_latest_reply",
              "read_ok_targeted", "read_ok_whole_page", "read_ok_latest_reply"):
        lines.append("  %-22s %s" % (k, s.get(k)))
    lines.append("-- per-tool detail --")
    for name in sorted(s.get("tools", {}).keys()):
        t = s["tools"][name]
        lines.append("  %-22s calls=%d ok=%d fail=%d" % (name, t["calls"], t["success"], t["failure"]))
    return "\n".join(lines)


def find_latest_run_log(root):
    """在 root 下递归找时间戳最大的一份 run_log_*.jsonl（方便 E2E 后直接汇总）。"""
    best, best_t = None, -1
    for dirpath, _, files in os.walk(root):
        for fn in files:
            if fn.startswith("run_log_") and fn.endswith(".jsonl"):
                ts = fn[len("run_log_"):-len(".jsonl")]
                try:
                    t = int(ts)
                except Exception:
                    continue
                if t > best_t:
                    best_t = t
                    best = os.path.join(dirpath, fn)
    return best


def write_summary_json(path, s):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)