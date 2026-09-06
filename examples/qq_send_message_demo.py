"""
qq_send_message_demo.py
第4组 · examples —— 真实 QQ 桌面端全自动发消息（点击 + 探针 + 视觉 OCR 门）

用途：
    演示重写后的真实技能 core.skill_library.app_send_message（点击 + 探针路线，
    不再是会被 QQ 判成"查看资料卡"的 Ctrl+F + Enter 键盘路线）：

        激活窗口 → 探针定位搜索框 → 输入群名关键词 → 逐行点搜索结果，
        每行裁剪"聊天标题区"交给**项目内视觉桥** OCR 比对群名（verify 回调，命中才继续）
        → 底部探针定位聊天输入框 → Ctrl+A 覆盖 → 粘贴消息 → 回车发送 → 截图取证

运行前置：
    1. 桌面 QQ 已登录并开着（经典布局）。
    2. 发送前的 OCR 门默认走项目内视觉桥 core.vision_bridge，它按 llm.supports_vision
       自动选源：主对话模型有视觉(true)→直接用主对话模型读图；纯文本(false)→读
       config.yaml 的 vision_bridge 段（独立第二个视觉 API）。没配好时会打印配置指引
       并退出，不会"没核对就盲发"。可用 --no-verify 跳过 OCR 门（直接点第一行结果
       就发，仅建议在"已知当前停在目标会话"的场景用）。
    3. 运行：

         python examples/qq_send_message_demo.py \
             --keyword "我的手机" --message "你好"
         python examples/qq_send_message_demo.py --no-verify --message "hello"

说明：
    - 发送动作属高风险操作；正式经 OSServiceAPI.run_skill_safely 调用时会先走
      确认门（safety HIGH）。本示例为直观演示直接调技能方法。
    - 演示的是机制，请对 --message 使用你真的想发的内容。
"""

import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.skill_library import SkillLibrary, make_ocr_verify  # noqa: E402


def _find_qq_rect(app):
    """先激活 QQ 再返回其窗口矩形（最小化/托盘时矩形是离屏小窗，裁图会失败）"""
    win = app.find_window(process="QQ") or app.find_window(title="QQ")
    if win is None:
        raise LookupError("未找到 QQ 窗口，请先登录并打开 QQ")
    app.activate_window(int(win["hwnd"]))
    time.sleep(1.0)
    return app.get_window_rect(int(win["hwnd"]))


def make_verify(keyword, rect):
    """
    构造发送前校验回调：项目内视觉桥 core.vision_bridge OCR 聊天标题（自动选源——
    主 llm 有视觉用它自己，否则 config.yaml 的 vision_bridge 段），必须包含关键词
    才放行；视觉源没配好时抛错并附配置指引。
    """
    gate = make_ocr_verify(keyword, rect)

    def verify(screenshot_path):
        ok = gate(screenshot_path)
        print(f"    [OCR门] 标题识别含'{keyword}'? {'✓ 命中' if ok else '✗ 未命中，试下一行'}")
        return ok

    return verify


def main():
    ap = argparse.ArgumentParser(description="真实 QQ 全自动发消息（点击+探针+OCR门）")
    ap.add_argument("--app", default="QQ")
    ap.add_argument("--keyword", default="我的手机",
                    help="要搜索并进入的会话/群名关键词（默认发给自己，演示安全）")
    ap.add_argument("--message", default="你好", help="要发送的消息")
    ap.add_argument("--no-verify", action="store_true",
                    help="跳过 OCR 门，直接点第一行结果就发（不推荐给陌生会话）")
    args = ap.parse_args()

    skills = SkillLibrary()
    app = skills.app

    rect = _find_qq_rect(app)
    if args.no_verify:
        verify = None
    else:
        try:
            verify = make_verify(args.keyword, rect)
        except Exception as e:
            print("构造 OCR 门失败，未发送任何消息：")
            print(str(e))
            return 2

    print(f"开始：搜索『{args.keyword}』→ 发送『{args.message}』"
          f"{'（含 OCR 门校验）' if verify else '（无 OCR 门，直接发第一行）'}")
    result = skills.call("app_send_message", {
        "app_name": args.app,
        "search_keyword": args.keyword,
        "message": args.message,
        "verify": verify,
    })
    print("结果：")
    for k, v in result.items():
        if k == "screenshot":
            print(f"  {k} = {v}")
        elif k == "verify_result":
            print(f"  {k} = {v}")
        elif k != "window":
            print(f"  {k} = {v}")
    if result.get("success"):
        print("OK —— 已在目标会话发送并截图取证")
    else:
        print("FAIL ——", result.get("error"), f"({result.get('error_code')})")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
