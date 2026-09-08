"""探针：验证 Ollama 流式响应的 Content-Type 是否缺 charset，
以及 requests 默认解码（resp.encoding）是否产生 mojibake。
"""
import json

import requests

URL = "http://localhost:11434/v1/chat/completions"
body = {
    "model": "huihui_ai/qwen2.5-abliterate:1.5b",
    "messages": [{"role": "user", "content": "只回复三个字：你好呀"}],
    "stream": True,
    "max_tokens": 20,
}

resp = requests.post(URL, json=body, timeout=120, stream=True)
print("HTTP", resp.status_code)
print("Content-Type:", resp.headers.get("Content-Type"))
print("requests 推断的 encoding:", repr(resp.encoding))

# 1) 模拟当前实现：iter_content(decode_unicode=True) 用 resp.encoding 解码
chunks = []
for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
    if chunk:
        chunks.append(chunk)
text_current = "".join(chunks)
print("\n[当前实现 decode_unicode=True] 前 120 字符:")
print(repr(text_current[:120]))

# 2) 正确做法：按字节读 + UTF-8 解码
resp2 = requests.post(URL, json=body, timeout=120, stream=True)
raw = b"".join(resp2.iter_content(chunk_size=None))
text_utf8 = raw.decode("utf-8")
print("\n[字节 + UTF-8 解码] 前 120 字符:")
print(repr(text_utf8[:120]))

# 3) 验证 mojibake 可逆性：latin-1 读回字节再按 UTF-8 解码
try:
    recovered = text_current.encode("latin-1").decode("utf-8")
    print("\nlatin-1 → utf-8 逆转验证:", repr(recovered[:80]))
    print("逆转后与直接 UTF-8 解码一致:", recovered == text_utf8)
except UnicodeEncodeError as e:
    print("\n逆转失败（说明除编码外还有别的损坏）:", e)

# 4) 提取 SSE data 行里的中文内容对比
def extract_content(ss):
    out = []
    for line in ss.split("\n"):
        line = line.strip()
        if line.startswith("data: ") and line[6:].strip() != "[DONE]":
            try:
                d = json.loads(line[6:])
                c = d.get("choices", [{}])[0].get("delta", {}).get("content")
                if c:
                    out.append(c)
            except Exception:
                pass
    return "".join(out)

print("\n当前实现提取的回复:", repr(extract_content(text_current)))
print("UTF-8 正解提取的回复:", repr(extract_content(text_utf8)))
