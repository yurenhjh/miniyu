"""快速基准：对本地 Ollama 模型测 prompt eval / 生成速度（tokens/s）"""
import json
import sys
import time
import urllib.request

PROMPT = "用三句话介绍你自己，简明扼要。"
URL = "http://localhost:11434/api/generate"


def bench(model):
    body = json.dumps({
        "model": model,
        "prompt": PROMPT,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 256},
    }).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.loads(r.read().decode())
    wall = time.time() - t0

    pe_cnt = data.get("prompt_eval_count", 0)
    pe_dur = data.get("prompt_eval_duration", 0) / 1e9
    ev_cnt = data.get("eval_count", 0)
    ev_dur = data.get("eval_duration", 0) / 1e9
    load_dur = data.get("load_duration", 0) / 1e9

    print(f"=== {model} ===")
    print(f"  总耗时 {wall:.1f}s | 模型加载 {load_dur:.1f}s")
    if pe_cnt and pe_dur:
        print(f"  prompt eval: {pe_cnt} tok / {pe_dur:.1f}s = {pe_cnt/pe_dur:.1f} tok/s")
    if ev_cnt and ev_dur:
        print(f"  生成速度:    {ev_cnt} tok / {ev_dur:.1f}s = {ev_cnt/ev_dur:.1f} tok/s")
    print(f"  回复预览: {data.get('response', '')[:80]!r}")
    print()


if __name__ == "__main__":
    for m in sys.argv[1:]:
        try:
            bench(m)
        except Exception as e:
            print(f"=== {m} === 失败: {e}\n")
