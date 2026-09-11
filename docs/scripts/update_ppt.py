# 更新第4组汇报PPT：1) 512→552 测试数；2) 追加 miniyu 系统设计信息页
import copy
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

PPTX = r"D:\Users\34808\Desktop\group4_tools_os_skills\docs\第4组汇报PPT.pptx"
p = Presentation(PPTX)

# ---------- 1) 全局替换测试数 512 -> 552（仅替换含数字的 run 文本） ----------
def num_of(run_text):
    # 只把整段中的 "512" 测试数替换；避免误伤（如 512MB）
    return run_text.replace("512", "552") if "512" in run_text else run_text

replaced = 0
for slide in p.slides:
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        for para in sh.text_frame.paragraphs:
            for run in para.runs:
                if "512" in run.text:
                    # 逐 run 处理：把含512的run按段落内替换
                    run.text = num_of(run.text)
                    replaced += 1

print("替换 512 的 run 数:", replaced)

# ---------- 从页8读取主题配色供新页复用 ----------
s8 = p.slides[7]
title_font_size = None
for sh in s8.shapes:
    if sh.has_text_frame and "模块设计" in sh.text_frame.text:
        for para in sh.text_frame.paragraphs:
            for run in para.runs:
                if run.text and run.font.size:
                    title_font_size = run.font.size
                    break
        break
print("页8 标题字号:", title_font_size)

# 主体内容配色（统一风格）
TITLE_COLOR = RGBColor(0x1F, 0x3A, 0x5F)    # 深蓝
ACCENT_COLOR = RGBColor(0xE8, 0x74, 0x3A)   # 橙红
BODY_COLOR = RGBColor(0x33, 0x33, 0x33)
BOTTOM_BAR = RGBColor(0xEE, 0xF2, 0xF6)

def add_content_slide(title, lines):
    """追加一页：顶部大标题 + 内容正文，底部装饰条，与现页风格一致"""
    slide = p.slides.add_slide(p.slide_layouts[6])  # Blank
    W, H = p.slide_width, p.slide_height

    # 底部装饰条
    from pptx.enum.shapes import MSO_SHAPE
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, p.slide_height - Emu(146304), p.slide_width, Emu(146304))
    bar.fill.solid(); bar.fill.fore_color.rgb = BOTTOM_BAR
    bar.line.fill.background()

    # 标题
    tb = slide.shapes.add_textbox(Emu(548640), Emu(320040), W - Emu(1097280), Emu(650000))
    tf = tb.text_frame; tf.word_wrap = True
    p0 = tf.paragraphs[0]
    r0 = p0.add_run(); r0.text = title
    r0.font.size = Pt(30); r0.font.bold = True; r0.font.color.rgb = TITLE_COLOR

    # 分隔线
    ln = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(548640), Emu(990600), W - Emu(1097280), Emu(27432))
    ln.fill.solid(); ln.fill.fore_color.rgb = ACCENT_COLOR; ln.line.fill.background()

    # 正文
    body = slide.shapes.add_textbox(Emu(640080), Emu(1371600), W - Emu(1280160), H - Emu(1828800))
    btf = body.text_frame; btf.word_wrap = True
    first = True
    for line in lines:
        para = btf.paragraphs[0] if first else btf.add_paragraph()
        first = False
        para.space_after = Pt(8)
        segs = line.split("\n")
        for si, seg in enumerate(segs):
            run = para.add_run()
            run.text = seg
            run.font.size = Pt(15)
            run.font.color.rgb = BODY_COLOR
            if seg.startswith("◆") or seg.startswith("▍"):
                run.font.bold = True
                run.font.size = Pt(16)
            if si < len(segs) - 1:
                para = btf.add_paragraph()
                para.space_after = Pt(8)
    return slide

# ---------- 2) 追加 miniyu 系统设计信息页 ----------

add_content_slide("miniyu 系统设计总览：配置化 + 分层架构", [
    "◆ 配置化设计（不写死，改配置即变更）\n  config.yaml：LLM 提供商 / 模型 / 授权档位 / 联网开关 / 协调层 / 邮件,全部可调；模板为 config.yaml.example（不含任何个人密钥）\n  优先级：环境变量 > config.yaml > 内置默认值",
    "◆ 分层架构\n  第1组 AI Shell/HostAgent（意图）→ 第5组 协调+安全 → 第2组 规划 → 第3组 执行 → 第4组 工具/技能\n  数据流 1→5→2→3→4，第4组统一输出 tool_result_json",
    "◆ OS 原理落地：持久性(文件系统/目录管理) 由第4组工具层直接承载",
])

add_content_slide("LLM 三层自动回退链（主 → 本地 → 确定性脑）", [
    "◆ 第 1 层（主）\n  任意 OpenAI 兼容 API：阿里百炼 qwen / DeepSeek / 豆包…  function-calling 驱动 59 工具 + 26 技能",
    "◆ 第 2 层（降级）\n  主 API 超时/鉴权失败/断网时自动切本地 Ollama（localhost），恢复后每 5 分钟自动重试主 API\n  本地小模型走 tool_free 纯对话模式（不带几十个工具 schema，省 CPU 解析数十秒）",
    "◆ 第 3 层（兜底）\n  确定性脑 DeterministicBrain：纯规则、零依赖、无需网络/API key，离线也能完整演示全流程\n  模型支持运行时热切换 + Web 下拉 + force_local_mode()",
])

add_content_slide("Web 交互设计：流式 + 折叠面板 + 主题", [
    "◆ SSE 实时流式\n  /chat 立即返 task_id → 后台 run_stream 逐 chunk 入队 → /task/<id>/events 推送\n  reasoning/token/tool_call/confirm/done + 15s 心跳",
    "◆ DeepSeek 式折叠\n  思考完成折叠「已深度思考(用时 Xs)」；工具调用折叠「工具调用(N 个)」，限高 260px 内部滚动\n  红色「⏹ 停止」按钮可打断死循环/超长输出（两层 stop_check + 幂等断电点）",
    "◆ 主题 + 语音\n  深/浅色 CSS 变量切换 + localStorage 记忆 + 首帧防闪烁；语音用 Web Speech API（Chrome/Edge），连续监听不漏字",
])

add_content_slide("授权档位 + 四层安全（base/advanced/full + 沙箱）", [
    "◆ 统一安全模型\n  网络安全总开关 web_search（开/关一切联网能力）+ 三档授权 agent.authorization",
    "◆ 三档授权\n  base 基础授权：全部高危操作需确认；advanced 高级授权：仅永久删除文件需确认；full 全自动：从不确认(含删除)\n  Web 顶栏下拉可运行时切换",
    "◆ 危险指令硬拦截（SafetySandbox 接入执行链）\n  rm/format/shutdown/dd/mkfs/fdisk/Stop-Computer 等 → 绕过确认门直接拒绝\n  实测：shutdown 不再弹确认窗即被拦截；正常操作不受影响（552 测试全绿）",
])

add_content_slide("可移植性与密钥安全（Windows/Linux 双平台）", [
    "◆ 双平台同一套代码\n  相对路径 + pathlib + platform.system() 自动适配；app_controller 分 Windows/Linux；browser_controller 走跨平台 CDP",
    "◆ 密钥不入库\n  config.yaml（含真实 key/授权码）git skip-worktree 保护不入库；分发只带 config.yaml.example 空模板\n  Ubuntu 一键：setup_linux.sh 装依赖 + 生成配置 + 检测 Xorg/Wayland",
    "◆ 资源效率\n  工具结果超长截断(默认4000字符) + HTTP 连接池(省 TCP/TLS 握手) + 历史会话侧栏(最近访问排序)",
])

p.save(PPTX)
print("已保存，总页数:", len(p.slides._sldIdLst))