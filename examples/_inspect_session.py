import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "conversations/d6eef332.json"
with open(path, encoding="utf-8") as f:
    data = json.load(f)
msgs = data.get("messages", [])
print("total:", len(msgs))
for i, m in enumerate(msgs):
    c = m.get("content", "")
    if not isinstance(c, str):
        c = json.dumps(c, ensure_ascii=False)
    tc = m.get("tool_calls")
    tag = f" tool_calls={len(tc)}" if tc else ""
    print(f"--- [{i}] role={m.get('role')}{tag} len={len(c)}")
    print(c[:150].replace("\n", " "))
