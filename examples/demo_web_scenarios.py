"""驱动 miniyu Web 后端逐场景演示，收集工具调用链与最终结果。

用法：先启动 examples/miniyu_web.py，再运行本脚本。
等价于用户在浏览器聊天框逐条输入指令（走同一套 /chat + SSE 链路）。
"""
import json
import time
import urllib.request

BASE = "http://localhost:5000"


def fetch_stream(url, method="GET", payload=None, timeout=180):
    req = urllib.request.Request(url, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(payload).encode("utf-8")
    else:
        data = None
    return urllib.request.urlopen(req, data=data, timeout=timeout)


def run_message(message):
    """POST /chat 拿 task_id，再流式消费 /task/<id>/events。返回 (tool_calls, final_text)"""
    resp = json.loads(fetch_stream(f"{BASE}/chat", method="POST",
                                   payload={"message": message}).read())
    task_id = resp["task_id"]
    tools = []
    final = ""
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            r = fetch_stream(f"{BASE}/task/{task_id}/events", timeout=60)
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip())
                t = ev.get("type")
                if t == "tool_call":
                    tools.append({"tool": ev.get("tool"), "args": ev.get("arguments")})
                elif t == "token":
                    continue
                elif t == "reasoning":
                    continue
                elif t in ("done", "error"):
                    final = ev.get("result", "")
                    return tools, final, ev.get("type")
        except Exception as e:
            final += f"\n[流异常:{e}]"
            return tools, final, "error"
    return tools, final, "timeout"


SCENARIOS = [
    ("场景1-GUI打开文件管理器", "打开文件管理器"),
    ("场景2-导航到目录", "导航到 D:\\Users\\34808\\Desktop\\group4_tools_os_skills"),
    ("场景3-创建文件夹", "在 D:\\Users\\34808\\Desktop\\group4_tools_os_skills 下创建一个名为 project 的文件夹"),
    ("场景4-文本编辑", "打开 D:\\Users\\34808\\Desktop\\group4_tools_os_skills 里的 test.txt 并添加一行 'demo line from miniyu'"),
    ("场景5-窗口切换", "切换到浏览器窗口"),
    ("场景6-整理下载目录", "整理 D:\\Users\\34808\\Desktop\\group4_tools_os_skills\\demo_downloads 里的下载文件，把它们按类型分类到子文件夹"),
]


def main():
    report = {}
    for name, msg in SCENARIOS:
        print(f"\n{'='*70}\n▶ {name}：「{msg}」\n{'='*70}")
        tools, final, status = run_message(msg)
        report[name] = {"status": status, "tools": tools, "final": final}
        if tools:
            print("  工具调用链：")
            for tc in tools:
                arg = json.dumps(tc["args"], ensure_ascii=False)
                print(f"    · {tc['tool']}({arg[:120]})")
        else:
            print("  （无工具调用）")
        print(f"  终态={status}  最终回复: {final[:300]}")
        time.sleep(1)
    # 汇总
    print("\n\n" + "=" * 70)
    print("演示汇总")
    print("=" * 70)
    for name, r in report.items():
        n = len(r["tools"])
        print(f"  {name}: 状态={r['status']} 工具调用={n} 条")
    with open("doc_demo_result.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()