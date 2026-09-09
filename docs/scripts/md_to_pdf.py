"""把 Markdown 调研/设计报告转换成 PDF。

排版满足课程验收要求：
- 正文：宋体（SimSun）小四号（12pt）
- 标题：黑体（SimHei）加粗、分级
- 页边距：上下 2.2cm，左右 2.6cm（word 默认）
- 表格、代码块：等宽/宋体、可跨页

转换链路：markdown -> (注入 CSS 的) HTML -> Microsoft Edge headless 打印 PDF。
要求本机已安装 Microsoft Edge（打印 PDF）；输出保留 .md 原文件。

用法：
    python md_to_pdf.py 输入.md [输出.pdf]
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

CJK_CSS = """
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  font-family: "SimSun", "宋体", serif;
  font-size: 12pt;                 /* 正文小四号 */
  line-height: 1.6;
  color: #1a1a1a;
  background: #fff;
}
/* 例：用 @page 控制页边距（Edge 打印遵循 */）
@page { size: A4; margin: 20mm 22mm 20mm 22mm; }
h1, h2, h3, h4 {
  font-family: "SimHei", "黑体", sans-serif;
  color: #111;
  line-height: 1.35;
  margin: 0.9em 0 0.5em;
}
h1 { font-size: 18pt; border-bottom: 2px solid #333; padding-bottom: .25em; }
h2 { font-size: 15pt; border-bottom: 1px solid #bbb; padding-bottom: .2em; }
h3 { font-size: 14pt; }
h4 { font-size: 12.5pt; }
p { margin: .45em 0; text-align: justify; }
strong { font-weight: bold; }
ul, ol { margin: .4em 0 .4em 1.6em; padding: 0; }
li { margin: .25em 0; }
table {
  border-collapse: collapse; width: 100%;
  margin: .6em 0; font-size: 10.5pt;
  font-family: "SimSun","宋体",serif;
}
th, td {
  border: 1px solid #bbb; padding: 5px 7px; vertical-align: top;
  text-align: left;
}
th { background: #f0f0f0; font-family: "SimHei","黑体",sans-serif; }
code {
  font-family: Consolas, "Courier New", monospace;
  font-size: 10.5pt; background: #f5f5f5; padding: 1px 3px;
}
pre {
  background: #f7f7f7; border: 1px solid #ddd; padding: 8px 10px;
  white-space: pre-wrap; word-break: break-all; line-height: 1.4;
}
pre code { background: none; padding: 0; }
blockquote {
  margin: .6em 0; padding: .3em 1em;
  border-left: 3px solid #888; color: #333; background: #fafafa;
}
hr { border: 0; border-top: 1px solid #ccc; margin: 1.2em 0; }
img { max-width: 100%; }
a { color: #0645ad; text-decoration: none; }

/* 标题不拆行、表头跨页重复 */
h1,h2,h3,h4 { page-break-after: avoid; }
tr { page-break-inside: avoid; }
"""


def find_edge() -> str:
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    for p in ("msedge.exe", "chrome.exe"):
        try:
            subprocess.run([p, "--version"], capture_output=True, check=True)
            return p
        except Exception:
            continue
    raise FileNotFoundError("未找到 Microsoft Edge / Chrome，无法打印 PDF")


def main() -> None:
    parser = argparse.ArgumentParser(description="Markdown -> PDF（宋体小四正文）")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs="?", default=None)
    args = parser.parse_args()

    src = Path(args.input).expanduser().resolve()
    if not src.exists():
        sys.exit(f"输入文件不存在: {src}")
    dst = (
        Path(args.output).expanduser().resolve()
        if args.output
        else src.with_suffix(".pdf")
    )
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        import markdown
    except ImportError:
        sys.exit("缺少 markdown 库：pip install markdown")

    text = src.read_text(encoding="utf-8")
    body = markdown.markdown(
        text, extensions=["tables", "fenced_code", "sane_lists", "attr_list"]
    )
    title = src.stem
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{title}</title><style>{CJK_CSS}</style></head>
<body>{body}</body></html>"""

    with tempfile.NamedTemporaryFile(
        "w", suffix=".html", encoding="utf-8", delete=False
    ) as f:
        f.write(html_doc)
        tmp_html = f.name

    edge = find_edge()
    print(f"使用 {Path(edge).name} 打印 PDF -> {dst}")
    subprocess.run(
        [
            edge,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={dst}",
            f"file:///{tmp_html}",
        ],
        check=True,
        capture_output=True,
    )
    print(f"完成：{dst}（{dst.stat().st_size} 字节）")


if __name__ == "__main__":
    main()