# 更新第4组汇报PPT数据看板与全绿测试数至最终态：552→558、Mock 57→59、测试文件 17→23
from pptx import Presentation

PPTX = r"D:\Users\34808\Desktop\group4_tools_os_skills\docs\第4组汇报PPT.pptx"
p = Presentation(PPTX)

# 1) 全局：测试数 552 -> 558（单独 token 或含前后缀），552 在 PPT 中只代表测试数
replaced_552 = 0
for slide in p.slides:
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        for para in sh.text_frame.paragraphs:
            for run in para.runs:
                if "552" in run.text:
                    run.text = run.text.replace("552", "558")
                    replaced_552 += run.text.count("558")

print("替换 552 -> 558 的 run 数:", replaced_552)

# 2) 数据看板页（第12页，索引11）：Mock 工具 57->59、测试文件 17->23
dash = p.slides[11]
for sh in dash.shapes:
    if not sh.has_text_frame:
        continue
    for para in sh.text_frame.paragraphs:
        for run in para.runs:
            if run.text.strip() == "57":
                run.text = "59"
            elif run.text.strip() == "17":
                run.text = "23"

# 3) Mock 页（第11页，索引10）："57 个工具" -> "59 个工具"
mock_s = p.slides[10]
for sh in mock_s.shapes:
    if sh.has_text_frame:
        for para in sh.text_frame.paragraphs:
            for run in para.runs:
                if "57 个工具" in run.text:
                    run.text = run.text.replace("57 个工具", "59 个工具")

p.save(PPTX)
print("已保存。总页数:", len(p.slides._sldIdLst))